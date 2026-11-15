"""従業員ポータルでの本人の年次有給休暇管理簿の閲覧（LVR-63）。本人の分だけを返す。"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate

from japan_hr_compliance.jp_leave.utils import CONSUMPTION, GRANT, OBLIGATION, ST_ACTIVE, ST_EXPIRED, ST_EXTINGUISHED, fl, today_

EXPIRY_SOON_DAYS = 90


def employee_for_session():
	if frappe.session.user == "Guest":
		frappe.throw(_("ログインしてください。"), frappe.PermissionError)
	from japan_hr_compliance.jp_hr_core.consent import employee_for_session as _employee

	return _employee()


def my_leave(employee):
	"""本人の付与・取得・残日数・失効予定・年5日の状況。"""
	today = today_()
	grants = frappe.get_all(GRANT, filters={"employee": employee, "docstatus": 1,
		"status": ("in", (ST_ACTIVE, ST_EXPIRED, ST_EXTINGUISHED))},
		fields=["name", "grant_date", "days", "used_days", "remaining_days", "lapsed_days", "expiry_date", "status", "grant_kind"],
		order_by="grant_date desc")
	takes = frappe.get_all(CONSUMPTION, filters={"employee": employee, "cancelled": 0},
		fields=["leave_date", "days", "half_day_part", "take_kind", "grant"], order_by="leave_date desc")
	active = [g for g in grants if g.status == ST_ACTIVE]
	soon = [g for g in active if fl(g.remaining_days) > 0 and getdate(g.expiry_date) <= add_days(today, EXPIRY_SOON_DAYS)]
	obligations = frappe.get_all(OBLIGATION, filters={"employee": employee, "status": ("!=", "無効")},
		fields=["period_start", "period_end", "required_days", "taken_days", "planned_days", "shortfall", "status"],
		order_by="period_start desc")
	return frappe._dict(
		balance=fl(sum(fl(g.remaining_days) for g in active)), grants=grants, takes=takes, expiring=soon,
		obligations=obligations, today=today,
	)
