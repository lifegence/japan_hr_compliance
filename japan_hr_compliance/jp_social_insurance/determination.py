"""標準報酬月額の決定の集計と候補の抽出（SIR-40〜48）。

- 給与実績（確定済みの給与明細）から支払月ごとの報酬・支払基礎日数を集計する（SIR-40）。
  報酬は「社保の報酬区分」が報酬の支給項目（年4回以上の賞与区分の項目を含む）、現物は
  現物区分の項目、遡及差額は遡及差額区分の項目。賞与明細は含めない
- 固定的賃金の変動は、固定的賃金区分の項目の月額（日割前の default_amount）の前月との差で
  検知する（給与構造の変更・手当の新設や廃止を含む。SIR-44）。非固定的賃金だけの増減は候補にしない
- 決定の判定は計算エンジン（calc.determination）が行い、確定は担当者の操作による（SIR-46）
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate

from japan_hr_compliance.jp_social_insurance import calc
from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import employee_data as ed
from japan_hr_compliance.jp_social_insurance.masters import dec, month_start
from japan_hr_compliance.jp_social_insurance.premium import (
	component_meta,
	is_remuneration_component,
)

DOCTYPE = "JP Remuneration Determination"
ZERO = Decimal("0")


def months_for(doc) -> list[date]:
	"""処理の種別ごとの対象の支払月。"""
	t = doc.determination_type
	if t == C.DET_REGULAR:
		return [date(cint(doc.year), m, 1) for m in (4, 5, 6)]
	if t == C.DET_ANNUAL:
		start = date(cint(doc.year) - 1, 7, 1)
		return [getdate(add_months(start, i)) for i in range(12)]
	if t == C.DET_OCCASIONAL:
		start = month_start(doc.change_month)
		return [getdate(add_months(start, i)) for i in range(3)]
	if t in (C.DET_MATERNITY_END, C.DET_CHILDCARE_END):
		start = month_start(calc_next_day(doc.leave_end))
		return [getdate(add_months(start, i)) for i in range(3)]
	return []


def calc_next_day(d):
	return getdate(d) + timedelta(days=1)


def effective_month(doc) -> date:
	"""改定・決定の適用開始月。"""
	t = doc.determination_type
	if t in (C.DET_REGULAR, C.DET_ANNUAL):
		return date(cint(doc.year), 9, 1)
	return getdate(add_months(months_for(doc)[0], 3))


def regular_slips(employee, first: date, last_month: date):
	"""支払日が first〜last_month 末の確定済みの給与明細（賞与明細を除く）。"""
	last = getdate(add_months(last_month, 1)) - timedelta(days=1)
	return frappe.get_all("Salary Slip", filters={"employee": employee, "docstatus": 1,
		"posting_date": ("between", [first, last]), "jp_is_bonus": 0},
		fields=["name", "posting_date", "start_date", "end_date", "jp_payment_base_days"], order_by="posting_date asc")


def slip_details(slip_names):
	if not slip_names:
		return {}
	rows = frappe.get_all("Salary Detail", filters={"parent": ("in", slip_names), "parenttype": "Salary Slip",
		"parentfield": "earnings"}, fields=["parent", "salary_component", "amount", "default_amount"])
	out = defaultdict(list)
	for r in rows:
		out[r.parent].append(r)
	return out


def monthly_records(employee, months: list[date]) -> list[dict]:
	"""支払月ごとの報酬実績（SIR-40）。同じ月に複数の明細があれば合算する。"""
	if not months:
		return []
	slips = regular_slips(employee, months[0], months[-1])
	details = slip_details([s.name for s in slips])
	meta = component_meta({d.salary_component for rows in details.values() for d in rows})
	joining = frappe.db.get_value("Employee", employee, "date_of_joining")
	by_month = {}
	for s in slips:
		m = month_start(s.posting_date)
		rec = by_month.setdefault(m, {"pay_month": m, "salary_slip": s.name, "base_days": 0, "cash": ZERO,
			"in_kind": ZERO, "retroactive_pay": ZERO, "excluded_reason": None})
		rec["base_days"] = max(flt(rec["base_days"]), flt(s.jp_payment_base_days))
		for d in details.get(s.name, []):
			mt = meta.get(d.salary_component)
			if not is_remuneration_component(mt):
				continue
			amount = dec(flt(d.amount))
			if cint(mt.jp_in_kind):
				rec["in_kind"] += amount
			else:
				rec["cash"] += amount
			if cint(mt.jp_retroactive):
				rec["retroactive_pay"] += amount
		if joining and s.start_date and getdate(s.start_date) < getdate(joining) <= getdate(s.end_date):
			rec["excluded_reason"] = _("途中入社で1か月分に満たない月")
	return [by_month[m] for m in months if m in by_month]


def fixed_wage_by_month(employee, first: date, last: date) -> dict[date, Decimal]:
	"""固定的賃金の月額（日割前）を支払月ごとに返す（SIR-44）。"""
	slips = regular_slips(employee, first, last)
	details = slip_details([s.name for s in slips])
	meta = component_meta({d.salary_component for rows in details.values() for d in rows})
	out: dict[date, Decimal] = {}
	for s in slips:
		m = month_start(s.posting_date)
		total = out.get(m, ZERO)
		for d in details.get(s.name, []):
			mt = meta.get(d.salary_component)
			if mt and mt.jp_fixed_wage == C.FIXED and is_remuneration_component(mt):
				total += dec(flt(d.default_amount) or flt(d.amount))
		out[m] = total
	return out


def to_calc_records(doc) -> list[calc.MonthlyRemuneration]:
	return [calc.MonthlyRemuneration(getdate(r.pay_month), cint(flt(r.base_days)), dec(r.cash), dec(r.in_kind),
		dec(r.retroactive_pay), r.excluded_reason or None) for r in doc.months]


# ---------------------------------------------------------------------------
# 一括作成
# ---------------------------------------------------------------------------


def _insured_employees(company, on_date):
	names = []
	for emp in frappe.get_all("Employee", filters={"company": company, "status": ("in", ("Active", "Left"))}, pluck="name"):
		if ed.qualification_on(emp, C.INS_HEALTH, on_date) or ed.qualification_on(emp, C.INS_PENSION, on_date):
			names.append(emp)
	return names


def _exists(employee, determination_type, **filters):
	return frappe.db.exists(DOCTYPE, {"employee": employee, "determination_type": determination_type,
		"docstatus": ("<", 2), **filters})


def new_determination(employee, determination_type, **values):
	doc = frappe.get_doc({"doctype": DOCTYPE, "employee": employee, "determination_type": determination_type,
		"days_category": ed.days_category(employee, values.get("change_month") or values.get("leave_end")
			or date(cint(values.get("year") or getdate().year), 7, 1)), **values})
	doc.fill_months()
	doc.insert()
	return doc


@frappe.whitelist()
def create_regular_determinations(company: str, year: int):
	"""定時決定の対象者全員の処理を下書きで作る（SIR-40・43。手入力なしで3か月分を集計）。"""
	frappe.has_permission(DOCTYPE, "create", throw=True)
	year = cint(year)
	july1 = date(year, 7, 1)
	created = []
	for emp in _insured_employees(company, july1):
		if _exists(emp, C.DET_REGULAR, year=year):
			continue
		q = ed.qualification_on(emp, C.INS_HEALTH, july1) or ed.qualification_on(emp, C.INS_PENSION, july1)
		scheduled = frappe.get_all(DOCTYPE, filters={"employee": emp, "docstatus": ("<", 2), "applicable": 1,
			"determination_type": ("in", (C.DET_OCCASIONAL, C.DET_MATERNITY_END, C.DET_CHILDCARE_END)),
			"effective_from": ("between", [july1, date(year, 9, 1)])}, pluck="effective_from")
		if not calc.is_regular_determination_target(year, q[0], [getdate(d) for d in scheduled]):
			continue
		created.append(new_determination(emp, C.DET_REGULAR, year=year).name)
	return created


@frappe.whitelist()
def extract_occasional_candidates(company: str, as_of_month):
	"""随時改定の候補を抽出し、下書きの処理を作る（SIR-44）。

	変動月から3か月の給与が確定している者のうち、固定的賃金の月額が前月から変わった者が候補。
	"""
	frappe.has_permission(DOCTYPE, "create", throw=True)
	as_of = month_start(as_of_month)
	first = getdate(add_months(as_of, -15))
	created = []
	for emp in _insured_employees(company, as_of):
		fixed = fixed_wage_by_month(emp, first, as_of)
		months = sorted(fixed)
		for prev, cur in zip(months, months[1:]):
			if getdate(add_months(prev, 1)) != cur or fixed[cur] == fixed[prev]:
				continue
			if getdate(add_months(cur, 2)) > as_of or not all(getdate(add_months(cur, i)) in fixed for i in (1, 2)):
				continue
			if _exists(emp, C.DET_OCCASIONAL, change_month=cur):
				continue
			direction = C.DIRECTION_INCREASE if fixed[cur] > fixed[prev] else C.DIRECTION_DECREASE
			created.append(new_determination(emp, C.DET_OCCASIONAL, change_month=cur, direction=direction).name)
	return created


@frappe.whitelist()
def create_annual_average_assessments(company: str, year: int):
	"""年間平均による保険者算定の候補を示す（SIR-48）。

	その年の定時決定の処理がある者について年間平均を求め、定時決定との差が健保・厚年のいずれかで
	2等級以上の者に下書きを作る。季節的変動の有無・申立ての要否・本人の同意は担当者が入力する。
	"""
	frappe.has_permission(DOCTYPE, "create", throw=True)
	year = cint(year)
	created = []
	for reg in frappe.get_all(DOCTYPE, filters={"company": company, "determination_type": C.DET_REGULAR, "year": year,
			"docstatus": ("<", 2)}, fields=["name", "employee"]):
		if _exists(reg.employee, C.DET_ANNUAL, year=year):
			continue
		doc = frappe.get_doc({"doctype": DOCTYPE, "employee": reg.employee, "determination_type": C.DET_ANNUAL,
			"year": year, "regular_determination": reg.name,
			"days_category": frappe.db.get_value(DOCTYPE, reg.name, "days_category")})
		doc.fill_months()
		doc.run_calculation(ignore_seasonal=True)
		if any(abs(cint(d)) >= 2 for d in (doc.health_grade_diff, doc.pension_grade_diff)):
			doc.insert()
			created.append(doc.name)
	return created
