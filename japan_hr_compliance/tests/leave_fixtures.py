"""年次有給休暇（B-3）のテスト用データ。会社カレンダーは土日を休日とする架空のもの。"""

from datetime import date, timedelta

import frappe
from frappe.utils import getdate, now_datetime

from japan_hr_compliance.jp_leave import jobs
from japan_hr_compliance.jp_leave.setup import after_migrate
from japan_hr_compliance.jp_leave.utils import ANNUAL_LEAVE_TYPE, GRANT, SETTINGS
from japan_hr_compliance.tests import fixtures as fx

HOLIDAY_LIST = "JP年休テスト（土日）"
HR_USER = "jp-leave-hr@example.com"
CAL_START, CAL_END = date(2024, 1, 1), date(2032, 12, 31)


def ensure_all():
	fx.ensure_all()
	after_migrate()
	ensure_holiday_list()
	ensure_settings()
	fx.ensure_user(HR_USER, ["JP HR User", "HR Manager", "HR User"])


def ensure_holiday_list():
	if not frappe.db.exists("Holiday List", HOLIDAY_LIST):
		hl = frappe.get_doc({"doctype": "Holiday List", "holiday_list_name": HOLIDAY_LIST, "from_date": CAL_START,
			"to_date": CAL_END})
		d = CAL_START
		while d <= CAL_END:
			if d.weekday() >= 5:
				hl.append("holidays", {"holiday_date": d, "description": "週休日", "weekly_off": 1})
			d += timedelta(days=1)
		hl.insert(ignore_permissions=True)
	if not frappe.db.exists("Holiday List Assignment", {"assigned_to": fx.COMPANY, "docstatus": 1}):
		doc = frappe.get_doc({"doctype": "Holiday List Assignment", "applicable_for": "Company", "assigned_to": fx.COMPANY,
			"holiday_list": HOLIDAY_LIST, "from_date": CAL_START})
		doc.insert(ignore_permissions=True)
		doc.submit()


def ensure_settings(**values):
	"""テスト会社の JP Leave Settings を既定値に戻す（values で上書き）。"""
	base = dict(grant_method="個別基準日", annual_leave_type=ANNUAL_LEAVE_TYPE, plan_days_before=7, confirm_method="自動",
		validity_years=2, container_years=40, half_day_allowed=1, overlap_method="按分", retention_years=5,
		notice_1_months=6, notice_2_months=9, notice_3_months_before_end=2, uniform_base_dates=[])
	base.update(values)
	if frappe.db.exists(SETTINGS, fx.COMPANY):
		doc = frappe.get_doc(SETTINGS, fx.COMPANY)
	else:
		doc = frappe.new_doc(SETTINGS)
		doc.company = fx.COMPANY
	dates = base.pop("uniform_base_dates")
	doc.update(base)
	doc.set("uniform_base_dates", [{"base_month": m, "base_day": d} for m, d in dates])
	doc.save(ignore_permissions=True)
	return doc


def is_workday(d):
	return d.weekday() < 5


def workdays(start, end):
	start, end = getdate(start), getdate(end)
	return [start + timedelta(days=i) for i in range((end - start).days + 1) if is_workday(start + timedelta(days=i))]


def make_employee(joining, contract=True, **contract_kw):
	emp = fx.make_employee(joining=joining)
	if contract:
		fx.make_contract(emp.name, joining, **contract_kw)
	return emp


def mark_attendance(employee, days, status="Present", leave_type=None, day_class=None):
	"""確定済みの Attendance を一括で作る（未来日・大量の日付を速く作るため直接登録する）。"""
	emp = frappe.db.get_value("Employee", employee, ["employee_name", "company"], as_dict=True)
	now = now_datetime()
	fields = ["name", "employee", "employee_name", "company", "attendance_date", "status", "leave_type", "jp_day_class",
		"docstatus", "creation", "modified", "owner", "modified_by", "naming_series"]
	values = []
	for d in days:
		values.append((f"JPTEST-ATT-{frappe.generate_hash(length=12)}", employee, emp.employee_name, emp.company, getdate(d),
			status, leave_type, day_class, 1, now, now, "Administrator", "Administrator", "HR-ATT-.YYYY.-"))
	frappe.db.bulk_insert("Attendance", fields, values)


def present_except(employee, start, end, absent=(), others=None):
	"""所定労働日を出勤とし、absent の日は欠勤、others={日付: (status, leave_type, day_class)} はその区分にする。"""
	others = {getdate(k): v for k, v in (others or {}).items()}
	absent = {getdate(d) for d in absent}
	present, absents = [], []
	for d in workdays(start, end):
		if d in others:
			continue
		(absents if d in absent else present).append(d)
	mark_attendance(employee, present)
	mark_attendance(employee, absents, "Absent")
	for d, (status, leave_type, day_class) in others.items():
		mark_attendance(employee, [d], status, leave_type, day_class)


def grant(employee, grant_date, days, round_no=None, kind=None, reason="テスト用の付与", checked=1, submit=True):
	"""手動入力の年休付与（前倒し・分割・既存の付与の登録など）。"""
	doc = frappe.get_doc({"doctype": GRANT, "employee": employee, "grant_date": grant_date, "days": days, "is_manual": 1,
		"round_no": round_no, "grant_kind": kind or "法定どおり", "reason": reason, "manual_checked": checked})
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def apply_leave(employee, from_date, to_date=None, half_day=0, part=None, kind="本人の請求", submit=True, leave_type=None,
		status="Approved"):
	doc = frappe.get_doc({
		"doctype": "Leave Application", "employee": employee, "leave_type": leave_type or ANNUAL_LEAVE_TYPE,
		"company": fx.COMPANY, "from_date": from_date, "to_date": to_date or from_date, "half_day": half_day,
		"half_day_date": from_date if half_day else None, "jp_half_day_part": part, "jp_take_kind": kind,
		"status": status, "posting_date": from_date,
	})
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def emp_rows(*employees):
	return [frappe.db.get_value("Employee", e, ["name", "company", "date_of_joining"], as_dict=True) for e in employees]


def run_daily(as_of, *employees):
	"""日次処理を as_of の日付で実行する（付与予定の作成・確定は指定した従業員に限る）。"""
	frappe.flags.jp_leave_today = getdate(as_of)
	jobs.daily(as_of, emp_rows(*employees) if employees else None)


def remaining(name):
	return frappe.db.get_value(GRANT, name, "remaining_days")
