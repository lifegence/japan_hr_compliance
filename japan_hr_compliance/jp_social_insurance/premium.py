"""給与明細1件分の社会保険料・雇用保険料・労災保険料を計算する（SIR-20〜33、50〜52、60〜65）。

計算そのものは計算エンジン（calc）が行い、ここでは DB から版・資格・標準報酬月額・休業を集めて
渡し、結果を給与明細の控除行・事業主負担行・明細行（JP SI Premium Line）の形にする。

- 月額：支給月と控除月の設定（翌月／当月）から保険料の対象月を決め、対象月ごとに
  その月の料率・標準報酬月額で計算する（退職時の2か月控除を単純な2倍にしない）
- 賞与：賞与区分の支給項目の合計から標準賞与額を求め、健保の年度累計・厚年の月上限で抑える
- 雇用保険・労災：雇用保険・労災の対象区分の支給項目の合計に料率を掛ける
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from japan_hr_compliance.jp_social_insurance import calc, exemption, masters
from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import employee_data as ed
from japan_hr_compliance.jp_social_insurance.masters import (
	Named,
	dec,
	month_start,
	select_named,
)

ZERO = Decimal("0")


@dataclass
class SlipResult:
	lines: list[dict] = field(default_factory=list)
	deductions: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
	employer: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
	target_months: tuple = ()
	deferred_months: tuple = ()
	warnings: list[str] = field(default_factory=list)
	bonus: calc.BonusPremium | None = None

	def warn(self, message):
		if message not in self.warnings:
			self.warnings.append(message)


# ---------------------------------------------------------------------------
# 支給項目の区分（Salary Component の Custom Field）
# ---------------------------------------------------------------------------


def component_meta(names) -> dict[str, frappe._dict]:
	names = list({n for n in names if n})
	if not names:
		return {}
	rows = frappe.get_all("Salary Component", filters={"name": ("in", names)},
		fields=["name", "jp_si_component_kind", "jp_si_pay_class", "jp_annual_payments", "jp_fixed_wage", "jp_in_kind",
			"jp_ei_target", "jp_retroactive"])
	return {r.name: r for r in rows}


def is_bonus_component(meta) -> bool:
	"""賞与区分で、賃金規程上の年間支給回数が4回以上でないもの（SIR-33）。"""
	if not meta or meta.jp_si_pay_class != C.PAY_CLASS_BONUS:
		return False
	return not calc.counts_as_remuneration(cint(meta.jp_annual_payments) or None)


def is_remuneration_component(meta) -> bool:
	"""社保の報酬（標準報酬月額の算定対象）か。年4回以上の賞与区分の項目も報酬に含める。"""
	if not meta or meta.jp_si_component_kind:
		return False
	if meta.jp_si_pay_class in (None, "", C.PAY_CLASS_REMUNERATION):
		return True
	return meta.jp_si_pay_class == C.PAY_CLASS_BONUS and not is_bonus_component(meta)


def is_ei_target(meta) -> bool:
	return bool(meta) and not meta.jp_si_component_kind and meta.jp_ei_target != C.EI_EXCLUDED


def classify_earnings(slip) -> frappe._dict:
	meta = component_meta(r.salary_component for r in slip.get("earnings", []))
	out = frappe._dict(bonus=ZERO, ei_wage=ZERO, bonus_components=[], meta=meta)
	for row in slip.get("earnings", []):
		m = meta.get(row.salary_component)
		amount = dec(flt(row.amount))
		if is_bonus_component(m):
			out.bonus += amount
			out.bonus_components.append(row.salary_component)
		if is_ei_target(m):
			out.ei_wage += amount
	return out


# ---------------------------------------------------------------------------
# 明細行
# ---------------------------------------------------------------------------


def _line(category, target, kind, base, rate, share: calc.PremiumShare | None, *, version: Named | None = None,
		rate_doctype=None, grade_table=None, exempt=False, note=None, rate_row=None):
	return {
		"category": category, "target_month": target, "premium_kind": kind, "base_amount": float(base or 0),
		"rate": float(rate * 100) if rate is not None else 0, "exempt": 1 if exempt else 0,
		"total_amount": float(share.total) if share else 0, "employee_amount": float(share.employee) if share else 0,
		"employer_amount": float(share.employer) if share else 0,
		"rate_doctype": rate_doctype if version else None, "rate_version": version.name if version else None,
		"rate_row": rate_row, "grade_table": grade_table, "note": note,
	}


def _premium_lines(result: SlipResult, category, target, premium, h_named, p_named, health_rates, pension_rates):
	"""月額・賞与の保険料（calc の MonthlyPremium / BonusPremium）を明細行にする。"""
	h_base = getattr(premium, "health_standard", None)
	if h_base is None:
		h_base = getattr(premium, "health_standard_bonus", None)
	p_base = getattr(premium, "pension_standard", None)
	if p_base is None:
		p_base = getattr(premium, "pension_standard_bonus", None)
	exempt = premium.exempt
	hc = premium.health_care
	if hc is not None or (exempt and h_named and h_base):
		kind = C.P_HEALTH_CARE if premium.care_applies else C.P_HEALTH
		rate = None
		if health_rates is not None:
			rate = health_rates.health + (health_rates.care if premium.care_applies else ZERO)
		result.lines.append(_line(category, target, kind, h_base, rate, hc.combined if hc else None, version=h_named,
			rate_doctype="JP Health Insurance Rate", exempt=exempt))
		if hc is not None and hc.combined.employee_exceeds_employer:
			result.warn(_("{0} {1}：本人負担が事業主負担を1円未満上回っています（50銭ルール。2.2 の検出のみ）")
				.format(target, kind))
	if premium.support is not None or (exempt and h_named and h_base and health_rates is not None and health_rates.support > 0):
		result.lines.append(_line(category, target, C.P_SUPPORT, h_base, health_rates.support if health_rates else None,
			premium.support, version=h_named, rate_doctype="JP Health Insurance Rate", exempt=exempt))
	if premium.pension is not None or (exempt and p_named and p_base):
		result.lines.append(_line(category, target, C.P_PENSION, p_base, pension_rates.pension if pension_rates else None,
			premium.pension, version=p_named, rate_doctype="JP Pension Rate", exempt=exempt))
		if premium.pension is not None and premium.pension.employee_exceeds_employer:
			result.warn(_("{0} 厚生年金：本人負担が事業主負担を1円未満上回っています（50銭ルール。2.2 の検出のみ）")
				.format(target))
	if premium.child_contribution is not None or (exempt and p_named and p_base):
		result.lines.append(_line(category, target, C.P_CHILD, p_base,
			pension_rates.child_contribution if pension_rates else None, premium.child_contribution, version=p_named,
			rate_doctype="JP Pension Rate", exempt=exempt))


def _add_statement(result: SlipResult, premium, settings):
	"""控除行（本人負担）と事業主負担行の金額を加える。表示方法は会社設定（SIR-21・22）。"""
	lines = calc.statement_lines(premium, care_display=C.DISPLAY_TO_CALC[settings.care_display],
		support_display=C.DISPLAY_TO_CALC[settings.support_display])
	for line in lines:
		kind = C.LINE_KEY_TO_KIND[line.key]
		if line.key == "health_care" and not premium.care_applies:
			kind = C.K_HEALTH
		result.deductions[kind] += line.employee
	if premium.health_care is not None:
		result.employer[C.K_ER_HEALTH] += premium.health_care.combined.employer
	if premium.support is not None:
		result.employer[C.K_ER_SUPPORT] += premium.support.employer
	if premium.pension is not None:
		result.employer[C.K_ER_PENSION] += premium.pension.employer
	if premium.child_contribution is not None:
		result.employer[C.K_ER_CHILD] += premium.child_contribution.employer


def _set_grade_tables(lines, target):
	"""月額の明細行に、対象月の等級表の版（健保・厚年）を記録する（7章「使用した等級表の版」）。"""
	names = {}
	for kind in (C.TABLE_HEALTH, C.TABLE_PENSION):
		try:
			names[kind] = masters.select_grade_table(kind, target).name
		except LookupError:
			names[kind] = None
	for line in lines:
		kind = C.TABLE_PENSION if line["premium_kind"] in (C.P_PENSION, C.P_CHILD) else C.TABLE_HEALTH
		line["grade_table"] = names[kind]


def _throw(target, error):
	frappe.throw(_("{0} の社会保険料を計算できません：{1}").format(target, error), title=_("社会保険の計算"))


def _version_warnings(result, target: date, chosen: Named | None, doctype: str):
	if chosen is None:
		return
	if target.month >= 3 and doctype == "JP Health Insurance Rate" and chosen.effective_from < date(target.year, 3, 1):
		result.warn(_("{0}年3月分以降の健康保険料率の確認済の版がありません（{1} の版で計算しました。5.3）")
			.format(target.year, chosen.effective_from))
	draft = frappe.db.get_value(doctype, {"status": C.STATUS_DRAFT, "effective_from": ("between",
		[chosen.effective_from, target])}, "name")
	if draft and draft != chosen.name:
		result.warn(_("{0} に未確認（下書き）の版 {1} があります。確認済の版 {2} で計算しました。").format(target, draft, chosen.name))


# ---------------------------------------------------------------------------
# 月額（SIR-20〜26、50・52）
# ---------------------------------------------------------------------------


def compute_monthly(slip, emp, settings, result: SlipResult, periods):
	pay_month = month_start(slip.posting_date)
	quals = [q for ins in (C.INS_HEALTH, C.INS_PENSION) for q, _cat in ed.get_qualification_periods(slip.employee, ins)]
	if not quals:
		return
	is_final = bool(emp.relieving_date and getdate(emp.relieving_date) <= getdate(slip.end_date))
	deduct_two = bool(cint(settings.final_pay_two_months)) and not cint(slip.get("jp_si_cancel_two_months"))
	plan = calc.plan_deduction(pay_month, C.TIMING_TO_CALC[settings.deduction_timing], quals, is_final_pay=is_final,
		deduct_two_months=deduct_two)
	result.target_months = plan.target_months
	result.deferred_months = plan.deferred_months
	if not plan.target_months:
		return
	if not emp.date_of_birth:
		frappe.throw(_("従業員 {0} の生年月日が登録されていません。").format(slip.employee), title=_("社会保険の計算"))
	birth = getdate(emp.date_of_birth)
	exempt_months = calc.monthly_exemption_months(periods)
	history = ed.get_standard_history(slip.employee)
	h_versions = masters.load_health_versions()
	p_versions = masters.load_pension_versions()
	for m in plan.target_months:
		hq = ed.qualification_for_month(slip.employee, C.INS_HEALTH, m)
		pq = ed.qualification_for_month(slip.employee, C.INS_PENSION, m)
		h_ins = bool(hq and hq.is_premium_month(m))
		prefecture = ed.prefecture_for(slip.employee, m) if h_ins else ""
		try:
			premium = calc.calculate_premium_for_month(m, birth_date=birth, prefecture=prefecture,
				health_qualification=hq, pension_qualification=pq, standard_history=history,
				health_rate_versions=[n.version for n in h_versions], pension_rate_versions=[n.version for n in p_versions],
				exempt_months=exempt_months)
		except (LookupError, ValueError) as e:
			_throw(_("{0}年{1}月分").format(m.year, m.month), e)
		h_named = select_named(h_versions, m) if premium.health_standard is not None else None
		p_named = select_named(p_versions, m) if premium.pension_standard is not None else None
		h_rates = h_named.version.rates_for(prefecture) if h_named else None
		p_rates = p_named.version.rates if p_named else None
		first = len(result.lines)
		_premium_lines(result, C.LINE_MONTHLY, m, premium, h_named, p_named, h_rates, p_rates)
		_set_grade_tables(result.lines[first:], m)
		_add_statement(result, premium, settings)
		_version_warnings(result, m, h_named, "JP Health Insurance Rate")
		if premium.exempt:
			result.warn(_("{0}年{1}月分は産前産後休業・育児休業等により免除しました。").format(m.year, m.month))


# ---------------------------------------------------------------------------
# 賞与（SIR-30〜33、51）
# ---------------------------------------------------------------------------


def bonus_entries(employee, fiscal_year, exclude_slip=None):
	name = frappe.db.get_value("JP Bonus Accumulation", {"employee": employee, "fiscal_year": fiscal_year}, "name")
	if not name:
		return ZERO, []
	acc = frappe.get_doc("JP Bonus Accumulation", name)
	return dec(acc.prior_employer_amount), [e for e in acc.entries if e.salary_slip != exclude_slip]


def compute_bonus(slip, emp, settings, result: SlipResult, periods, earnings):
	if earnings.bonus <= 0:
		return
	pay_date = getdate(slip.posting_date)
	hq = ed.qualification_for_month(slip.employee, C.INS_HEALTH, pay_date)
	pq = ed.qualification_for_month(slip.employee, C.INS_PENSION, pay_date)
	if hq is None and pq is None:
		return
	if not emp.date_of_birth:
		frappe.throw(_("従業員 {0} の生年月日が登録されていません。").format(slip.employee), title=_("社会保険の計算"))
	h_versions = masters.load_health_versions()
	p_versions = masters.load_pension_versions()
	try:
		caps = select_named(masters.load_bonus_caps(), pay_date)
	except LookupError as e:
		_throw(_("賞与（{0}）").format(pay_date), e)
	h_named = p_named = h_rates = p_rates = None
	if hq is not None and hq.is_premium_month(pay_date):
		try:
			h_named = select_named(h_versions, pay_date)
			h_rates = h_named.version.rates_for(ed.prefecture_for(slip.employee, pay_date))
		except LookupError as e:
			_throw(_("賞与（{0}）").format(pay_date), e)
	if pq is not None and pq.is_premium_month(pay_date):
		try:
			p_named = select_named(p_versions, pay_date)
			p_rates = p_named.version.rates
		except LookupError as e:
			_throw(_("賞与（{0}）").format(pay_date), e)
	prior, entries = bonus_entries(slip.employee, calc_fiscal_year(pay_date), exclude_slip=slip.name)
	health_before = prior + sum((dec(e.health_standard_bonus) for e in entries if getdate(e.pay_date) <= pay_date), ZERO)
	pension_before = sum((dec(e.pension_standard_bonus) for e in entries
		if month_start(e.pay_date) == month_start(pay_date) and getdate(e.pay_date) <= pay_date), ZERO)
	try:
		bp = calc.calculate_bonus_premium(pay_date, earnings.bonus, birth_date=getdate(emp.date_of_birth),
			health_rates=h_rates, pension_rates=p_rates, caps=caps.version, health_qualification=hq,
			pension_qualification=pq, health_cumulative_before=health_before, pension_same_month_before=pension_before,
			exempt=calc.is_bonus_exempt(pay_date, periods))
	except (LookupError, ValueError) as e:
		_throw(_("賞与（{0}）").format(pay_date), e)
	result.bonus = bp
	if bp.health_treatment != calc.TREATMENT_PREMIUM:
		h_named = None
	if bp.pension_treatment != calc.TREATMENT_PREMIUM:
		p_named = None
	_premium_lines(result, C.LINE_BONUS, pay_date, bp, h_named, p_named, h_rates, p_rates)
	_add_statement(result, bp, settings)
	if bp.exempt:
		result.warn(_("賞与（{0}）は産前産後休業・育児休業等により免除しました。").format(pay_date))
	if calc.TREATMENT_LOSS_MONTH in (bp.health_treatment, bp.pension_treatment):
		result.warn(_("資格喪失月の賞与のため保険料はかかりません（健保の年度累計に算入し、賞与支払届の対象）。"))
	bonus_frequency_warnings(slip, result, earnings)


def calc_fiscal_year(d) -> int:
	d = getdate(d)
	return d.year if d.month >= 4 else d.year - 1


def bonus_frequency_warnings(slip, result, earnings):
	"""規程で回数が明らかでない賞与の項目で、7月1日以前1年間の支給が4回以上なら警告（SIR-33）。"""
	pay_date = getdate(slip.posting_date)
	july1 = date(pay_date.year, 7, 1) if pay_date >= date(pay_date.year, 7, 1) else date(pay_date.year - 1, 7, 1)
	for comp in set(earnings.bonus_components):
		if cint(earnings.meta[comp].jp_annual_payments):
			continue
		dates = frappe.db.sql("""select ss.posting_date from `tabSalary Slip` ss
			join `tabSalary Detail` sd on sd.parent = ss.name and sd.parenttype = 'Salary Slip' and sd.parentfield = 'earnings'
			where ss.employee = %s and ss.docstatus = 1 and sd.salary_component = %s and ss.name != %s""",
			(slip.employee, comp, slip.name or ""), pluck=True)
		dates = [getdate(d) for d in dates] + [pay_date]
		if calc.bonus_frequency_warning(dates, july1):
			result.warn(_("支給項目 {0} は7月1日以前1年間に4回以上支給されています。賃金規程上の年間支給回数を確認してください（年4回以上は報酬。SIR-33）。")
				.format(comp))


# ---------------------------------------------------------------------------
# 雇用保険・労災（SIR-60〜64）
# ---------------------------------------------------------------------------


def compute_labour(slip, emp, settings, result: SlipResult, earnings):
	wage = earnings.ei_wage
	if wage <= 0:
		return
	if ed.officer_type_from_reason(emp.jp_si_exempt_reason) == C.OFFICER:
		return
	closing = getdate(slip.get("jp_si_closing_date") or slip.end_date)
	payment = getdate(slip.posting_date)
	is_bonus = bool(cint(slip.get("jp_is_bonus")))
	basis = calc.employment_insurance_basis_date(closing_date=closing, payment_date=payment,
		basis=C.BASIS_TO_CALC[settings.ei_rate_basis], is_bonus=is_bonus)
	insured = ed.insured_during(slip.employee, C.INS_EMPLOYMENT, slip.start_date, slip.end_date)
	if insured:
		business = ed.ei_business_type(slip.employee, closing)
		try:
			named = select_named(masters.load_employment_rates(), basis, by_month=False,
				predicate=lambda v: v.business_type == business)
		except LookupError as e:
			_throw(_("雇用保険（{0}）").format(basis), e)
		prem = calc.calculate_employment_insurance(wage, named.version, insured=True)
		share = calc.PremiumShare(prem.employee + prem.employer, prem.employee, prem.employer)
		line = _line(C.LINE_EMPLOYMENT, basis, C.P_EMPLOYMENT, wage, named.version.employee_rate, share, version=named,
			rate_doctype="JP Employment Insurance Rate", note=_("事業主負担率 {0}/1000").format(named.version.employer_permille))
		result.lines.append(line)
		result.deductions[C.K_EMPLOYMENT] += prem.employee
		result.employer[C.K_ER_EMPLOYMENT] += prem.employer
	business_code = ed.workers_comp_business(slip.employee, closing)
	if not business_code:
		result.warn(_("所属事業場に労災保険の事業の種類が登録されていないため、労災保険料を計上していません。"))
		return
	try:
		named = select_named(masters.load_workers_comp_versions(business_code), basis, by_month=False)
	except LookupError as e:
		_throw(_("労災保険（{0}）").format(basis), e)
	share = calc.employer_only(wage, named.version.rate)
	result.lines.append(_line(C.LINE_WORKERS_COMP, basis, C.P_WORKERS_COMP, wage, named.version.rate, share,
		version=Named(business_code, named.version), rate_doctype="JP Workers Comp Rate", rate_row=named.name))
	result.employer[C.K_ER_WORKERS_COMP] += share.employer


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def compute_for_slip(slip) -> SlipResult:
	"""給与明細の社会保険料・雇用保険料・労災保険料を計算する（DB は読むだけ）。"""
	result = SlipResult()
	emp = frappe.db.get_value("Employee", slip.employee, ["date_of_birth", "date_of_joining", "relieving_date",
		"jp_si_exempt_reason"], as_dict=True)
	settings = ed.get_settings(slip.company)
	earnings = classify_earnings(slip)
	periods = exemption.get_leave_periods(slip.employee)
	if not cint(slip.get("jp_is_bonus")):
		compute_monthly(slip, emp, settings, result, periods)
	compute_bonus(slip, emp, settings, result, periods, earnings)
	compute_labour(slip, emp, settings, result, earnings)
	return result
