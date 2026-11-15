"""使用者の時季指定（LVR-54、労基則24条の6）。

意見聴取の日・方法・本人の希望と指定日を記録し、確定（Submit）で取得区分「時季指定」の年休の申請を作成・承認する。
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_years, getdate

from japan_hr_compliance.jp_leave.utils import annual_leave_type_for, get_settings, throw_retention, today_


class JPLeaveDesignation(Document):
	def validate(self):
		if getdate(self.hearing_date) > getdate(self.designated_date):
			frappe.throw(_("意見聴取日は指定日以前の日付にしてください。"))
		if self.half_day and not self.half_day_part:
			frappe.throw(_("半日の場合は午前・午後を選んでください。"))
		if self.half_day and not get_settings(self.company).half_day_allowed:
			frappe.throw(_("この会社は半日単位の年次有給休暇を認めていません。"))
		if self.obligation:
			start, end, emp = frappe.db.get_value("JP Five Day Obligation", self.obligation,
				["period_start", "period_end", "employee"])
			if emp != self.employee:
				frappe.throw(_("履行期間 {0} は別の従業員のものです。").format(self.obligation))
			if not (getdate(start) <= getdate(self.designated_date) <= getdate(end)):
				frappe.throw(_("指定日は履行期間（{0}〜{1}）の中の日にしてください。").format(start, end))

	def on_submit(self):
		la = frappe.get_doc({
			"doctype": "Leave Application", "employee": self.employee, "company": self.company,
			"leave_type": annual_leave_type_for(self.company), "from_date": self.designated_date,
			"to_date": self.designated_date, "half_day": 1 if self.half_day else 0,
			"half_day_date": self.designated_date if self.half_day else None, "jp_half_day_part": self.half_day_part,
			"jp_take_kind": "時季指定", "status": "Approved",
			"leave_approver": frappe.db.get_value("Employee", self.employee, "leave_approver"),
			"description": _("使用者の時季指定（{0}）。意見聴取 {1}（{2}）").format(self.name, self.hearing_date, self.hearing_method),
		})
		la.flags.ignore_permissions = True
		la.insert()
		la.submit()
		self.db_set("leave_application", la.name)

	def on_cancel(self):
		if self.leave_application:
			la = frappe.get_doc("Leave Application", self.leave_application)
			if la.docstatus == 1:
				la.flags.ignore_permissions = True
				la.cancel()

	def on_trash(self):
		if self.docstatus == 0:
			return
		years = int(get_settings(self.company).retention_years or 5)
		until = add_years(getdate(self.designated_date), max(years, 3) + 1)
		if getdate(today_()) <= until:
			throw_retention(self.doctype, until)
