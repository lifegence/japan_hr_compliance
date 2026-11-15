"""社会保険・雇用保険（B-5）のテスト用マスタ・給与設定・従業員。

料率・等級表・上限の値はすべて「例示値」（計算規則 v1.0 の 5.4・6章、計算エンジンのテストの
fixtures と同じ）。実際の値ではないため、運用では公表資料に基づく値をマスタに登録すること。何度呼んでもよい。
"""

from datetime import date

import frappe
from frappe.utils import getdate

from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance.calc.tests import fixtures as cf
from japan_hr_compliance.tests import fixtures as fx

SOURCE = {"source_title": "テスト用の例示値（計算規則 v1.0 5.4）", "source_published": "2026-02-01",
	"source_url": "https://example.invalid/rates", "confirmed_by": "Administrator", "confirmed_on": "2026-02-10"}
TOKYO = "東京都"
WC_CODE = "T94"

BASIC = "テスト基本給"
COMMUTE = "テスト通勤手当"
OVERTIME = "テスト残業手当"
BONUS = "テスト賞与"
SEVERANCE = "テスト退職金"
RETRO = "テスト遡及差額"
STRUCTURE = "テスト月給（社保）"
HOLIDAY_LIST = "テスト休日（社保）"
LEAVE_CHILDCARE = "テスト育児休業"
LEAVE_MATERNITY = "テスト産前産後休業"
LEAVE_POSTNATAL = "テスト出生時育児休業"


def _confirmed(doc):
	doc.update({"status": C.STATUS_CONFIRMED, **SOURCE})
	return doc


def ensure_version(doctype, filters, values, confirmed=True):
	name = frappe.db.get_value(doctype, filters, "name")
	if name:
		return frappe.get_doc(doctype, name)
	doc = frappe.get_doc({"doctype": doctype, **filters, **values})
	if confirmed:
		_confirmed(doc)
	doc.insert(ignore_permissions=True)
	return doc


def _grades(rows):
	out = []
	for i, (standard, lower) in enumerate(rows):
		upper = rows[i + 1][1] if i + 1 < len(rows) else None
		out.append({"grade": i + 1, "standard": standard, "lower": lower, "upper": upper})
	return out


def ensure_masters():
	ensure_version("JP Standard Remuneration Table", {"kind": C.TABLE_HEALTH, "effective_from": "2020-09-01"},
		{"grades": _grades(cf._HEALTH_ROWS)})
	ensure_version("JP Standard Remuneration Table", {"kind": C.TABLE_PENSION, "effective_from": "2020-09-01"},
		{"grades": _grades(cf._PENSION_ROWS)})
	for eff, health, care, support in (("2025-03-01", 9.91, 1.59, 0), ("2026-03-01", 9.85, 1.62, 0),
			("2026-04-01", 9.85, 1.62, 0.23)):
		ensure_version("JP Health Insurance Rate", {"effective_from": eff, "insurer": C.INSURER_KYOKAI},
			{"rates": [{"prefecture": TOKYO, "health_rate": health, "care_rate": care, "support_rate": support}]})
	ensure_version("JP Pension Rate", {"effective_from": "2017-09-01"}, {"pension_rate": 18.3, "child_contribution_rate": 0.36})
	ensure_version("JP Bonus Cap", {"effective_from": "2016-04-01"}, {"health_annual_cap": 5730000, "pension_monthly_cap": 1500000})
	for eff, ee, er in (("2025-04-01", 5.5, 9), ("2026-04-01", 5, 8.5)):
		ensure_version("JP Employment Insurance Rate", {"effective_from": eff, "business_type": C.EI_GENERAL},
			{"employee_permille": ee, "employer_permille": er})
	if not frappe.db.exists("JP Workers Comp Rate", WC_CODE):
		frappe.get_doc({"doctype": "JP Workers Comp Rate", "business_code": WC_CODE, "business_name": "テスト その他の各種事業",
			"rates": [{"effective_from": "2024-04-01", "rate_permille": 3, "status": C.STATUS_CONFIRMED, **SOURCE}]}
		).insert(ignore_permissions=True)
	ensure_version("JP SI Eligibility Rule", {"effective_from": "2024-10-01"}, {"wage_requirement": 1,
		"wage_threshold": 88000, "company_size_threshold": 51, "sh_weekly_hours": 20, "ei_weekly_hours": 20})
	ensure_version("JP SI Eligibility Rule", {"effective_from": "2026-10-01"}, {"wage_requirement": 0,
		"company_size_threshold": 51, "sh_weekly_hours": 20, "ei_weekly_hours": 20})


def set_settings(**values):
	defaults = {"deduction_timing": C.TIMING_NEXT, "final_pay_two_months": 1, "care_display": C.DISPLAY_COMBINED,
		"support_display": C.DISPLAY_SEPARATE, "ei_rate_basis": C.BASIS_CLOSING, "absence_deduction_monthly": 0,
		"prescribed_days_basis": C.PRESCRIBED_SLIP, "specific_applicable_office": 0, "specific_applicable_from": None}
	defaults.update(values)
	if frappe.db.exists("JP SI Settings", fx.COMPANY):
		doc = frappe.get_doc("JP SI Settings", fx.COMPANY)
	else:
		doc = frappe.get_doc({"doctype": "JP SI Settings", "company": fx.COMPANY})
	doc.update(defaults)
	doc.save(ignore_permissions=True)
	return doc


def ensure_component(name, abbr, ctype="Earning", **values):
	if frappe.db.exists("Salary Component", name):
		doc = frappe.get_doc("Salary Component", name)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return doc
	return frappe.get_doc({"doctype": "Salary Component", "salary_component": name, "salary_component_abbr": abbr,
		"type": ctype, "depends_on_payment_days": 0, "is_tax_applicable": 0, **values}).insert(ignore_permissions=True)


def ensure_fiscal_years():
	from japan_compliance.tests.fixtures import ensure_fiscal_year

	for year in range(2024, 2029):
		ensure_fiscal_year(f"{year}-SI", f"{year}-01-01", f"{year}-12-31", fx.COMPANY)


def ensure_payroll():
	ensure_fiscal_years()
	ensure_component(BASIC, "TBASE", jp_si_pay_class=C.PAY_CLASS_REMUNERATION, jp_fixed_wage=C.FIXED,
		jp_ei_target=C.EI_TARGET)
	ensure_component(COMMUTE, "TCOMM", jp_si_pay_class=C.PAY_CLASS_REMUNERATION, jp_fixed_wage=C.FIXED,
		jp_ei_target=C.EI_TARGET)
	ensure_component(OVERTIME, "TOVT", jp_si_pay_class=C.PAY_CLASS_REMUNERATION, jp_fixed_wage=C.NON_FIXED,
		jp_ei_target=C.EI_TARGET)
	ensure_component(BONUS, "TBON", jp_si_pay_class=C.PAY_CLASS_BONUS, jp_annual_payments=2, jp_ei_target=C.EI_TARGET)
	ensure_component(SEVERANCE, "TSEV", jp_si_pay_class=C.PAY_CLASS_EXCLUDED, jp_ei_target=C.EI_EXCLUDED)
	ensure_component(RETRO, "TRETRO", jp_si_pay_class=C.PAY_CLASS_REMUNERATION, jp_fixed_wage=C.NON_FIXED,
		jp_retroactive=1, jp_ei_target=C.EI_TARGET)
	if not frappe.db.exists("Holiday List", HOLIDAY_LIST):
		frappe.get_doc({"doctype": "Holiday List", "holiday_list_name": HOLIDAY_LIST, "from_date": "2024-01-01",
			"to_date": "2028-12-31", "holidays": [{"holiday_date": "2024-01-01", "description": "元日"}]}
		).insert(ignore_permissions=True)
	frappe.db.set_value("Company", fx.COMPANY, "default_holiday_list", HOLIDAY_LIST)
	if not frappe.db.exists("Holiday List Assignment", {"applicable_for": "Company", "assigned_to": fx.COMPANY,
			"docstatus": 1}):
		hla = frappe.get_doc({"doctype": "Holiday List Assignment", "applicable_for": "Company", "assigned_to": fx.COMPANY,
			"holiday_list": HOLIDAY_LIST, "from_date": "2024-01-01"})
		hla.insert(ignore_permissions=True)
		hla.submit()
	payable = frappe.db.get_value("Company", fx.COMPANY, "default_payroll_payable_account")
	if not payable:
		payable = frappe.db.get_value("Account", {"company": fx.COMPANY, "is_group": 0, "root_type": "Liability",
			"account_type": "Payable"}, "name") or frappe.db.get_value("Account", {"company": fx.COMPANY, "is_group": 0,
			"root_type": "Liability"}, "name")
		frappe.db.set_value("Company", fx.COMPANY, "default_payroll_payable_account", payable)
	if not frappe.db.exists("Salary Structure", STRUCTURE):
		doc = frappe.get_doc({"doctype": "Salary Structure", "name": STRUCTURE, "company": fx.COMPANY,
			"payroll_frequency": "Monthly", "currency": "JPY", "is_active": "Yes",
			"earnings": [{"salary_component": BASIC, "abbr": "TBASE", "amount_based_on_formula": 1, "formula": "base",
				"depends_on_payment_days": 0}],
			"deductions": []})
		doc.insert(ignore_permissions=True)
		doc.submit()
	for name, kind in ((LEAVE_CHILDCARE, C.LEAVE_CHILDCARE), (LEAVE_MATERNITY, C.LEAVE_MATERNITY),
			(LEAVE_POSTNATAL, C.LEAVE_POSTNATAL)):
		if not frappe.db.exists("Leave Type", name):
			frappe.get_doc({"doctype": "Leave Type", "leave_type_name": name, "is_lwp": 1, "allow_negative": 1,
				"include_holiday": 1, "jp_si_exemption_kind": kind}).insert(ignore_permissions=True)
	wp = frappe.get_doc("JP Workplace", fx.WORKPLACE_HQ)
	if wp.get("jp_si_prefecture") != TOKYO or wp.wc_business_type != WC_CODE:
		wp.jp_si_prefecture = TOKYO
		wp.wc_business_type = WC_CODE
		wp.save(ignore_permissions=True)


def ensure_all():
	fx.ensure_all()
	ensure_masters()
	ensure_payroll()
	set_settings()


def make_employee(joining="2025-04-01", birth="1981-05-10", base=300000, health=170000, pension=None,
		relieving=None, insured=True, ei=True, history_from=None, contract=None, **kw):
	"""社保・雇保の被保険者（資格履歴・標準報酬月額・給与構造の割当つき）を作る。"""
	emp = fx.make_employee(joining=joining, date_of_birth=birth, holiday_list=HOLIDAY_LIST, **kw)
	fx.make_contract(emp.name, joining, **(contract or {}))
	if insured:
		for ins in (C.INS_HEALTH, C.INS_PENSION):
			add_status(emp.name, ins, joining)
		add_history(emp.name, history_from or joining, health, pension if pension is not None else health)
	if ei:
		add_status(emp.name, C.INS_EMPLOYMENT, joining)
	ssa = frappe.get_doc({"doctype": "Salary Structure Assignment", "employee": emp.name, "salary_structure": STRUCTURE,
		"company": fx.COMPANY, "from_date": joining, "base": base, "currency": "JPY",
		"payroll_payable_account": frappe.db.get_value("Company", fx.COMPANY, "default_payroll_payable_account")})
	ssa.insert(ignore_permissions=True)
	ssa.submit()
	if relieving:
		set_relieving(emp.name, relieving)
	return emp


def set_relieving(employee, relieving):
	from japan_hr_compliance.jp_social_insurance.calc import loss_date_for_retirement

	frappe.db.set_value("Employee", employee, "relieving_date", relieving)
	lost = loss_date_for_retirement(getdate(relieving))
	for row in frappe.get_all("JP Employee Insurance Status", filters={"employee": employee, "lost": ("is", "not set")},
			pluck="name"):
		doc = frappe.get_doc("JP Employee Insurance Status", row)
		doc.lost = lost
		doc.save(ignore_permissions=True)


def add_status(employee, insurance_type, acquired, lost=None, category=C.CATEGORY_GENERAL):
	return frappe.get_doc({"doctype": "JP Employee Insurance Status", "employee": employee, "insurance_type": insurance_type,
		"acquired": acquired, "lost": lost, "insured_category": category}).insert(ignore_permissions=True)


def add_history(employee, effective_from, health, pension, decision=C.DECISION_ACQUISITION, submit=True):
	doc = frappe.get_doc({"doctype": "JP Standard Remuneration History", "employee": employee,
		"effective_from": effective_from, "decision_type": decision, "health_standard": health,
		"pension_standard": pension or None})
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def make_slip(employee, month, posting_day=25, submit=False, additional=None, **kw):
	"""month（YYYY-MM-01）の計算期間（月初〜月末）の給与明細。additional は [(component, amount), ...]。"""
	month = getdate(month)
	from japan_hr_compliance.jp_social_insurance.calc._util import month_end

	start, end = kw.pop("start_date", None) or month, month_end(month)
	posting = kw.pop("posting_date", None) or date(month.year, month.month, min(posting_day, end.day))
	doc = frappe.get_doc({"doctype": "Salary Slip", "employee": employee, "company": fx.COMPANY, "start_date": start,
		"end_date": kw.pop("end_date", end), "posting_date": posting, "payroll_frequency": "Monthly", "currency": "JPY",
		**kw})
	if additional:
		doc.run_method("get_emp_and_working_day_details")
		for component, amount in additional:
			abbr = frappe.db.get_value("Salary Component", component, "salary_component_abbr")
			doc.append("earnings", {"salary_component": component, "abbr": abbr, "amount": amount, "default_amount": amount})
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def lines(slip, category=None, kind=None):
	return [l for l in slip.jp_si_lines if (category is None or l.category == category)
		and (kind is None or l.premium_kind == kind)]


def deduction(slip, component):
	return sum(r.amount for r in slip.deductions if r.salary_component == component)


def employer(slip, component):
	return sum(r.amount for r in slip.employer_contributions if r.salary_component == component)
