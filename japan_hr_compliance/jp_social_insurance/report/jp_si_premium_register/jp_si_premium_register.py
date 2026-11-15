"""社会保険料・雇用保険料・労災保険料の個人別一覧（SIR-27・63〜65）。

事業主負担は1円未満を丸めずに示す。50銭ルールにより本人負担が事業主負担を上回る行に印を付ける
（2.2・9.1 #3。検出のみで金額は調整しない）。
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return columns(), data(filters)


def columns():
	return [
		{"fieldname": "salary_slip", "label": _("給与明細"), "fieldtype": "Link", "options": "Salary Slip", "width": 150},
		{"fieldname": "employee", "label": _("従業員"), "fieldtype": "Link", "options": "Employee", "width": 120},
		{"fieldname": "employee_name", "label": _("氏名"), "fieldtype": "Data", "width": 120},
		{"fieldname": "posting_date", "label": _("支給日"), "fieldtype": "Date", "width": 100},
		{"fieldname": "category", "label": _("区分"), "fieldtype": "Data", "width": 80},
		{"fieldname": "target_month", "label": _("対象月"), "fieldtype": "Date", "width": 100},
		{"fieldname": "premium_kind", "label": _("保険料"), "fieldtype": "Data", "width": 160},
		{"fieldname": "base_amount", "label": _("基礎額"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "rate", "label": _("料率（%）"), "fieldtype": "Float", "precision": 4, "width": 90},
		{"fieldname": "employee_amount", "label": _("本人負担"), "fieldtype": "Currency", "width": 100},
		{"fieldname": "employer_amount", "label": _("事業主負担"), "fieldtype": "Float", "precision": 2, "width": 110},
		{"fieldname": "exempt", "label": _("免除"), "fieldtype": "Check", "width": 60},
		{"fieldname": "exceeds", "label": _("本人負担＞事業主負担"), "fieldtype": "Check", "width": 140},
		{"fieldname": "rate_version", "label": _("料率の版"), "fieldtype": "Data", "width": 120},
	]


def data(filters):
	conditions = ["ss.docstatus = 1", "ss.company = %(company)s", "ss.posting_date between %(from_date)s and %(to_date)s"]
	if filters.employee:
		conditions.append("ss.employee = %(employee)s")
	rows = frappe.db.sql(f"""select ss.name as salary_slip, ss.employee, ss.employee_name, ss.posting_date, l.category,
			l.target_month, l.premium_kind, l.base_amount, l.rate, l.employee_amount, l.employer_amount, l.exempt,
			l.rate_version
		from `tabJP SI Premium Line` l join `tabSalary Slip` ss on ss.name = l.parent and l.parenttype = 'Salary Slip'
		where {' and '.join(conditions)} order by ss.posting_date, ss.employee, l.idx""", filters, as_dict=True)
	for r in rows:
		r.exceeds = 1 if flt(r.employee_amount) > flt(r.employer_amount, 6) and flt(r.employer_amount) > 0 else 0
	if filters.only_exceeds:
		rows = [r for r in rows if r.exceeds]
	return rows
