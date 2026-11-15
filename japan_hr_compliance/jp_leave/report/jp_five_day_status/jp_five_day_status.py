"""年5日の未達見込みの一覧（LVR-53）。履行期間の経過率・残り期間・未取得日数と到達した通知の段階。"""

import frappe
from frappe import _

from japan_hr_compliance.jp_leave.five_day_service import shortfall_list


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = [
		{"fieldname": "employee", "label": _("従業員"), "fieldtype": "Link", "options": "Employee", "width": 120},
		{"fieldname": "employee_name", "label": _("氏名"), "fieldtype": "Data", "width": 120},
		{"fieldname": "period_start", "label": _("期間開始"), "fieldtype": "Date", "width": 100},
		{"fieldname": "period_end", "label": _("期間終了"), "fieldtype": "Date", "width": 100},
		{"fieldname": "required_days", "label": _("必要日数"), "fieldtype": "Float", "width": 80},
		{"fieldname": "taken_days", "label": _("取得日数"), "fieldtype": "Float", "width": 80},
		{"fieldname": "planned_days", "label": _("取得予定"), "fieldtype": "Float", "width": 80},
		{"fieldname": "shortfall", "label": _("未取得日数"), "fieldtype": "Float", "width": 85},
		{"fieldname": "elapsed_rate", "label": _("経過率"), "fieldtype": "Percent", "width": 80},
		{"fieldname": "days_left", "label": _("残り日数"), "fieldtype": "Int", "width": 80},
		{"fieldname": "stage", "label": _("通知の段階"), "fieldtype": "Data", "width": 90},
		{"fieldname": "name", "label": _("履行期間"), "fieldtype": "Link", "options": "JP Five Day Obligation", "width": 130},
	]
	return columns, shortfall_list(filters.get("as_of"), filters.get("company"))
