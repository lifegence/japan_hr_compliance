"""健保の標準賞与額の年度累計（JP Bonus Accumulation。SIR-31・33）。

給与明細の確定で賞与1回分の標準賞与額を年度の累計に加え、取消で除く。資格喪失月の賞与
（保険料なし）も健保の累計には算入する。資格取得前・喪失後の支給は算入しない。
"""

import frappe
from frappe.utils import flt, getdate

from japan_hr_compliance.jp_social_insurance.premium import calc_fiscal_year

DOCTYPE = "JP Bonus Accumulation"


def get_or_create(employee: str, fiscal_year: int):
	name = frappe.db.get_value(DOCTYPE, {"employee": employee, "fiscal_year": fiscal_year}, "name")
	if name:
		return frappe.get_doc(DOCTYPE, name)
	doc = frappe.get_doc({"doctype": DOCTYPE, "employee": employee, "fiscal_year": fiscal_year})
	doc.insert(ignore_permissions=True)
	return doc


def update_accumulation(slip, cancel=False):
	if not flt(slip.get("jp_bonus_standard")):
		return
	fy = calc_fiscal_year(slip.posting_date)
	doc = get_or_create(slip.employee, fy)
	doc.set("entries", [e for e in doc.entries if e.salary_slip != slip.name])
	if not cancel:
		doc.append("entries", {
			"salary_slip": slip.name, "pay_date": getdate(slip.posting_date), "standard_bonus": slip.jp_bonus_standard,
			"health_standard_bonus": slip.jp_bonus_health_standard,
			"pension_standard_bonus": slip.jp_bonus_pension_standard,
			"treatment": slip.jp_bonus_health_treatment, "report_required": slip.jp_bonus_report_required,
		})
	doc.flags.ignore_permissions = True
	doc.save()
