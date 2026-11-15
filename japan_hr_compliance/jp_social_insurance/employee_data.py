"""従業員ごとの社会保険のデータ（資格・標準報酬月額・事業所・設定）を DB から集める
（SIR-10〜13）。

- 資格（取得日・喪失日）は JP Employee Insurance Status を正とし、行がない保険は
  B-1 の Employee の資格取得日・喪失日（jp_hi_acquired 等。EMP-11）を使う
- 所属事業場は B-1 の get_contract_at / get_workplace_at から引き、健保の料率の都道府県は
  社保の適用先の事業場（一括適用なら適用先）の都道府県で決める（SIR-02・10）
"""

from __future__ import annotations

from datetime import date

import frappe
from frappe import _
from frappe.utils import cint, getdate

from japan_hr_compliance.jp_hr_core.contract import get_contract_at, get_workplace_at
from japan_hr_compliance.jp_hr_core.workplace import (
	SCHEME_EI,
	SCHEME_SOCIAL,
	get_insurance_unit,
)
from japan_hr_compliance.jp_social_insurance import calc
from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance.masters import dec, month_start

STATUS_DOCTYPE = "JP Employee Insurance Status"
HISTORY_DOCTYPE = "JP Standard Remuneration History"
SETTINGS_DOCTYPE = "JP SI Settings"


# ---------------------------------------------------------------------------
# 会社別の設定
# ---------------------------------------------------------------------------


def get_settings(company: str) -> frappe._dict:
	"""JP SI Settings（なければ既定値）。"""
	values = None
	if company and frappe.db.exists(SETTINGS_DOCTYPE, company):
		values = frappe.db.get_value(SETTINGS_DOCTYPE, company, "*", as_dict=True)
	defaults = frappe._dict(
		company=company, deduction_timing=C.TIMING_NEXT, final_pay_two_months=1, care_display=C.DISPLAY_COMBINED,
		support_display=C.DISPLAY_SEPARATE, ei_rate_basis=C.BASIS_CLOSING, absence_deduction_monthly=0,
		prescribed_days_basis=C.PRESCRIBED_SLIP, specific_applicable_office=0, specific_applicable_from=None,
	)
	if values:
		defaults.update({k: v for k, v in values.items() if v is not None})
	return defaults


def is_specific_applicable_office(settings, on_date) -> bool:
	if not cint(settings.specific_applicable_office):
		return False
	return not settings.specific_applicable_from or getdate(settings.specific_applicable_from) <= getdate(on_date)


# ---------------------------------------------------------------------------
# 資格（SIR-11）
# ---------------------------------------------------------------------------


def get_status_rows(employee: str, insurance_type: str) -> list[frappe._dict]:
	return frappe.get_all(STATUS_DOCTYPE, filters={"employee": employee, "insurance_type": insurance_type},
		fields=["name", "acquired", "lost", "insured_category"], order_by="acquired asc")


def get_qualification_periods(employee: str, insurance_type: str) -> list[tuple[calc.Qualification, str]]:
	"""資格期間（calc.Qualification, 被保険者区分）の列。再取得があれば複数。"""
	rows = get_status_rows(employee, insurance_type)
	if rows:
		return [(calc.Qualification(getdate(r.acquired), getdate(r.lost) if r.lost else None),
			r.insured_category or C.CATEGORY_GENERAL) for r in rows]
	acquired_field, lost_field = C.B1_QUALIFICATION_FIELDS[insurance_type]
	values = frappe.db.get_value("Employee", employee, [acquired_field, lost_field], as_dict=True) or {}
	if not values.get(acquired_field):
		return []
	lost = values.get(lost_field)
	return [(calc.Qualification(getdate(values[acquired_field]), getdate(lost) if lost else None), C.CATEGORY_GENERAL)]


def qualification_for_month(employee: str, insurance_type: str, target_month) -> calc.Qualification | None:
	"""対象月を含む資格期間（なければ最も近い過去の期間、さらになければ None）。

	複数の期間がある場合、対象月が保険料の対象月となる期間を優先する。
	"""
	periods = get_qualification_periods(employee, insurance_type)
	if not periods:
		return None
	month = month_start(target_month)
	for q, _cat in periods:
		if q.is_premium_month(month):
			return q
	past = [q for q, _cat in periods if month_start(q.acquired) <= month]
	return past[-1] if past else periods[0][0]


def qualification_on(employee: str, insurance_type: str, on_date) -> tuple[calc.Qualification, str] | None:
	"""指定日に被保険者である資格期間と区分。"""
	d = getdate(on_date)
	for q, cat in get_qualification_periods(employee, insurance_type):
		if q.acquired <= d and (q.lost is None or d < q.lost):
			return q, cat
	return None


def insured_during(employee: str, insurance_type: str, start, end) -> tuple[calc.Qualification, str] | None:
	"""期間 [start, end] と重なる資格期間（雇用保険の判定用）。"""
	s, e = getdate(start), getdate(end)
	for q, cat in get_qualification_periods(employee, insurance_type):
		if q.acquired <= e and (q.lost is None or q.lost > s):
			return q, cat
	return None


# ---------------------------------------------------------------------------
# 標準報酬月額の履歴（SIR-12）
# ---------------------------------------------------------------------------


def get_standard_history(employee: str) -> list[calc.StandardRemuneration]:
	"""確定（Submit）済みの標準報酬月額の履歴。下書きは計算に使わない（SIR-46）。"""
	rows = frappe.get_all(HISTORY_DOCTYPE, filters={"employee": employee, "docstatus": 1},
		fields=["effective_from", "health_standard", "pension_standard", "decision_type"], order_by="effective_from asc")
	return [calc.StandardRemuneration(getdate(r.effective_from), dec(r.health_standard) if r.health_standard else None,
		dec(r.pension_standard) if r.pension_standard else None, r.decision_type) for r in rows]


def current_standard(employee: str, target_month) -> calc.StandardRemuneration | None:
	try:
		return calc.select_standard_remuneration(get_standard_history(employee), month_start(target_month))
	except LookupError:
		return None


# ---------------------------------------------------------------------------
# 事業所・都道府県（SIR-02・10）
# ---------------------------------------------------------------------------


def employment_date_in_month(employee: str, target_month) -> date:
	"""対象月のうち在籍している日（契約の参照に使う）。月末か退職日の早い方、入社月は入社日以降。"""
	month = month_start(target_month)
	end = calc_month_end(month)
	emp = frappe.db.get_value("Employee", employee, ["date_of_joining", "relieving_date"], as_dict=True)
	if emp.relieving_date and getdate(emp.relieving_date) < end:
		end = getdate(emp.relieving_date)
	if emp.date_of_joining and getdate(emp.date_of_joining) > end:
		end = getdate(emp.date_of_joining)
	return end


def calc_month_end(month: date) -> date:
	from japan_hr_compliance.jp_social_insurance.calc._util import month_end

	return month_end(month)


def social_insurance_workplace(employee: str, on_date) -> str | None:
	"""社保の適用先の事業場（一括適用承認なら適用先）。"""
	workplace = get_workplace_at(employee, on_date)
	if not workplace:
		return None
	unit = get_insurance_unit(workplace, SCHEME_SOCIAL, on_date)
	return unit.applied_workplace if unit else workplace


def prefecture_for(employee: str, target_month) -> str:
	"""対象月の健保の料率を決める都道府県（適用事業所の都道府県）。"""
	on_date = employment_date_in_month(employee, target_month)
	workplace = social_insurance_workplace(employee, on_date)
	if not workplace:
		frappe.throw(_("従業員 {0} の {1} 時点の雇用契約（所属事業場）がありません。").format(employee, on_date),
			title=_("社会保険の計算"))
	prefecture = frappe.db.get_value("JP Workplace", workplace, "jp_si_prefecture")
	if not prefecture:
		frappe.throw(_("事業場 {0} に社会保険の都道府県が登録されていません。").format(workplace), title=_("社会保険の計算"))
	return prefecture


def ei_business_type(employee: str, on_date) -> str:
	"""雇用保険の事業の種類（事業場の雇用保険の適用の行。なければ一般の事業）。"""
	workplace = get_workplace_at(employee, on_date)
	if workplace:
		unit = get_insurance_unit(workplace, SCHEME_EI, on_date)
		if unit and unit.ei_business_class:
			return unit.ei_business_class
	return C.EI_GENERAL


def workers_comp_business(employee: str, on_date) -> str | None:
	"""労災保険の業種（JP Workplace.wc_business_type → JP Workers Comp Rate）。"""
	workplace = get_workplace_at(employee, on_date)
	return frappe.db.get_value("JP Workplace", workplace, "wc_business_type") if workplace else None


def officer_type_from_reason(reason: str | None) -> str | None:
	"""B-1 の「被保険者でない場合の理由」（jp_si_exempt_reason）から役員区分を返す。役員でなければ None。"""
	return C.EXEMPT_REASON_TO_OFFICER.get(reason or "")


def officer_type(employee: str) -> str | None:
	return officer_type_from_reason(frappe.db.get_value("Employee", employee, "jp_si_exempt_reason"))


def days_category(employee: str, on_date) -> str:
	"""支払基礎日数の規則の区分（SIR-42）。

	厚年（なければ健保）の資格の被保険者区分が短時間労働者なら「短時間労働者」、
	一般で契約の雇用区分がパートタイマー・アルバイトなら「一般（4分の3以上のパート等）」。
	"""
	for ins in (C.INS_PENSION, C.INS_HEALTH):
		found = qualification_on(employee, ins, on_date)
		if found:
			if found[1] == C.CATEGORY_SHORT_TIME:
				return C.DAYS_SHORT_TIME
			break
	contract = get_contract_at(employee, on_date)
	if contract and contract.employment_class in ("パートタイマー", "アルバイト"):
		return C.DAYS_PART_TIME
	return C.DAYS_GENERAL
