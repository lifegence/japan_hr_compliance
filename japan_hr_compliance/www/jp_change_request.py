"""従業員ポータルの変更申請の画面（従業員マスタ要件 B-1 v1.0 EMP-86）。住所・振込口座・扶養親族の変更を申請する。"""

import frappe
from frappe import _

from japan_hr_compliance.jp_hr_core import change_request
from japan_hr_compliance.jp_hr_core.address import get_address_at
from japan_hr_compliance.jp_hr_core.consent import accounts_snapshot, employee_for_session

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("ログインしてください。"), frappe.PermissionError)
	employee = employee_for_session()
	context.title = _("登録内容の変更申請")
	context.employee_name = frappe.db.get_value("Employee", employee, "employee_name")
	address = get_address_at(employee, frappe.utils.today())
	context.current_address = address.full_address if address else ""
	context.accounts = accounts_snapshot(employee)
	context.dependents = frappe.get_all("JP Employee Dependent", filters={"parent": employee, "parenttype": "Employee"},
		fields=["name", "dependent_name", "relation"], order_by="idx asc")
	context.requests = change_request.get_pending_requests(employee)
	return context
