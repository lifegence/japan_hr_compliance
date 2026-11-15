"""JP Leave の共通処理（今日の日付・会社設定・日数の変換・通知）。

規則：年次有給休暇の付与・管理の規則（開発時の要件定義 B-3）。
純粋な計算は ``japan_hr_compliance.jp_leave.calc`` にあり、ここでは DB・hrms との結合だけを扱う。
"""

from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

SETTINGS = "JP Leave Settings"
GRANT = "JP Annual Leave Grant"
CONSUMPTION = "JP Annual Leave Consumption"
CALC = "JP Attendance Rate Calc"
OBLIGATION = "JP Five Day Obligation"
DESIGNATION = "JP Leave Designation"
GRANT_TABLE = "JP Leave Grant Table"

ANNUAL_LEAVE_TYPE = "年次有給休暇"
CATEGORY_ANNUAL = "年休"
CATEGORY_STATUTORY = "法定休暇"
CATEGORY_SPECIAL = "特別休暇"

# 年休付与の状態
ST_PLANNED, ST_PENDING, ST_NO_GRANT = "予定", "保留", "付与なし"
ST_ACTIVE, ST_EXPIRED, ST_EXTINGUISHED, ST_CANCELLED = "確定", "失効", "消滅", "取消"
DRAFT_STATES = (ST_PLANNED, ST_PENDING)

SETTINGS_DEFAULTS = frappe._dict(
	grant_method="個別基準日", annual_leave_type=ANNUAL_LEAVE_TYPE, plan_days_before=7, confirm_method="自動",
	validity_years=2, container_years=40, half_day_allowed=1, overlap_method="按分", care_leave_year_start_month=4,
	notice_1_months=6, notice_2_months=9, notice_3_months_before_end=2, retention_years=5,
	notice_1_text=None, notice_2_text=None, notice_3_text=None, uniform_base_dates=[],
)


def today_():
	"""処理上の今日。テストでは ``frappe.flags.jp_leave_today`` で差し替える。"""
	return getdate(frappe.flags.jp_leave_today or nowdate())


def D(value):
	"""Float の日数を Decimal に（0.5 日単位の誤差を避ける）。"""
	if isinstance(value, Decimal):
		return value
	return Decimal(str(flt(value, 3))).normalize() if value else Decimal(0)


def fl(value):
	return float(value or 0)


def get_settings(company):
	"""会社の JP Leave Settings。未作成なら既定値（LVR-01 の既定は個別基準日）。"""
	values = frappe._dict(SETTINGS_DEFAULTS)
	if company and frappe.db.exists(SETTINGS, company):
		doc = frappe.get_cached_doc(SETTINGS, company)
		for key in SETTINGS_DEFAULTS:
			value = doc.get(key)
			if value not in (None, "") or key in ("half_day_allowed",):
				values[key] = value
		values.uniform_base_dates = [(r.base_month, r.base_day) for r in doc.uniform_base_dates]
	values.company = company
	return values


def annual_leave_type_for(company):
	return get_settings(company).annual_leave_type or ANNUAL_LEAVE_TYPE


def is_annual_leave_type(leave_type):
	if not leave_type:
		return False
	return frappe.get_cached_value("Leave Type", leave_type, "jp_leave_category") == CATEGORY_ANNUAL


def hr_recipients():
	from japan_hr_compliance.jp_hr_core.contract import hr_users

	return hr_users()


def employee_user(employee):
	return frappe.db.get_value("Employee", employee, "user_id")


def manager_user(employee):
	reports_to = frappe.db.get_value("Employee", employee, "reports_to")
	return frappe.db.get_value("Employee", reports_to, "user_id") if reports_to else None


def notify(users, subject, doctype=None, name=None, message=None):
	"""Notification Log で通知する。送った宛先の一覧を返す。"""
	sent = []
	for user in sorted({u for u in users if u and u not in ("Guest",)}):
		if not frappe.db.get_value("User", user, "enabled"):
			continue
		frappe.get_doc({
			"doctype": "Notification Log", "for_user": user, "type": "Alert", "document_type": doctype,
			"document_name": name, "subject": subject, "email_content": message or subject,
		}).insert(ignore_permissions=True)
		sent.append(user)
	return sent


def throw_retention(doctype, until):
	frappe.throw(
		_("{0} は年次有給休暇管理簿の元データです。保存期間（{1} まで）内は削除できません（労基則24条の7）。").format(_(doctype), until),
		frappe.ValidationError, title=_("保存期間内のデータ"),
	)
