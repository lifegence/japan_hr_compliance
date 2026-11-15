"""従業員ポータル：自分の年次有給休暇管理簿（B-3 LVR-63）。ログイン中のユーザー本人の分だけを表示する。"""

import frappe
from frappe import _

from japan_hr_compliance.jp_leave.portal import employee_for_session, my_leave

no_cache = 1


def get_context(context):
	employee = employee_for_session()
	context.title = _("年次有給休暇")
	context.employee_name = frappe.db.get_value("Employee", employee, "employee_name")
	context.leave = my_leave(employee)
	return context
