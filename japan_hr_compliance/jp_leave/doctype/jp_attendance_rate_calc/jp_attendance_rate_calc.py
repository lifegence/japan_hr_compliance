"""出勤率の算定明細（LVR-15）。付与の確定に使った明細は変更・削除しない（後から勤怠を修正しても付与時点のまま）。"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from japan_hr_compliance.jp_leave.utils import GRANT, throw_retention, today_


class JPAttendanceRateCalc(Document):
	def validate(self):
		if not self.is_new() and self.frozen and self.has_value_changed("attended_days"):
			frappe.throw(_("確定済みの付与に使った算定明細は変更できません（LVR-15）。"))

	def on_trash(self):
		grant = frappe.db.get_value(GRANT, {"attendance_calc": self.name, "docstatus": ("in", (1, 2))},
			["name", "company", "grant_date", "expiry_date", "days"], as_dict=True)
		if not grant and not self.frozen:
			return
		from japan_hr_compliance.jp_leave.doctype.jp_annual_leave_grant.jp_annual_leave_grant import retention_until

		until = retention_until(grant) if grant else None
		if until is None or getdate(today_()) <= until:
			throw_retention(self.doctype, until or _("付与の保存期間"))
