"""保険料総額の試算と納入告知額の差異（社会保険・雇用保険の計算規則 v1.0 SIR-27、2.2）。

確定済みの給与明細の保険料の明細（JP SI Premium Line）を、保険料の対象月（月額）・支給月（賞与）で
集計し、事業所全体の保険料額（全額の合計の1円未満切捨て）＝納入告知額の試算と、個人別の事業主負担の
合計との差（端数の範囲）を示す。納入告知額を入力すると告知額との差も示す。差額は会社負担として
調整する（計上方法は B-8 で確定）。
"""

from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate

from japan_hr_compliance.jp_social_insurance import calc
from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance.masters import dec

GROUPS = (
	("health", _("健康保険（介護含む）"), (C.P_HEALTH_CARE, C.P_HEALTH), "notice_health"),
	("support", _("子ども・子育て支援金"), (C.P_SUPPORT,), "notice_support"),
	("pension", _("厚生年金"), (C.P_PENSION,), "notice_pension"),
	("child", _("子ども・子育て拠出金"), (C.P_CHILD,), "notice_child"),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return columns(), data(filters)


def columns():
	return [
		{"fieldname": "premium", "label": _("保険料"), "fieldtype": "Data", "width": 180},
		{"fieldname": "people", "label": _("人数"), "fieldtype": "Int", "width": 70},
		{"fieldname": "total", "label": _("全額の合計"), "fieldtype": "Float", "precision": 2, "width": 130},
		{"fieldname": "notice_amount", "label": _("納入告知額（試算）"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "employee_sum", "label": _("本人負担の合計"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "employer_payable", "label": _("事業主負担（試算）"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "individual_employer_sum", "label": _("個人別の事業主負担の合計"), "fieldtype": "Float", "precision": 2, "width": 150},
		{"fieldname": "difference", "label": _("差異（端数）"), "fieldtype": "Float", "precision": 2, "width": 100},
		{"fieldname": "actual_notice", "label": _("納入告知額（実際）"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "notice_difference", "label": _("告知額−試算"), "fieldtype": "Currency", "width": 110},
	]


def get_lines(company, month):
	month = getdate(month).replace(day=1)
	next_month = getdate(add_months(month, 1))
	return frappe.db.sql("""select l.premium_kind, l.category, l.total_amount, l.employee_amount, l.employer_amount,
			ss.employee
		from `tabJP SI Premium Line` l join `tabSalary Slip` ss on ss.name = l.parent
		where l.parenttype = 'Salary Slip' and ss.docstatus = 1 and ss.company = %(company)s and l.exempt = 0
			and l.category in ('月額', '賞与') and l.target_month >= %(month)s and l.target_month < %(next)s""",
		{"company": company, "month": month, "next": next_month}, as_dict=True)


def data(filters):
	lines = get_lines(filters.company, filters.month)
	out = []
	for _key, label, kinds, notice_field in GROUPS:
		rows = [r for r in lines if r.premium_kind in kinds]
		shares = [calc.PremiumShare(dec(r.total_amount), dec(r.employee_amount), dec(r.employer_amount)) for r in rows]
		rec = calc.reconcile_with_notice(shares)
		actual = flt(filters.get(notice_field)) or None
		out.append({
			"premium": label, "people": len({r.employee for r in rows}), "total": float(sum((s.total for s in shares), Decimal(0))),
			"notice_amount": float(rec.notice_amount), "employee_sum": float(rec.employee_sum),
			"employer_payable": float(rec.employer_payable), "individual_employer_sum": float(rec.individual_employer_sum),
			"difference": float(rec.difference), "actual_notice": actual,
			"notice_difference": (actual - float(rec.notice_amount)) if actual is not None else None,
		})
	return out
