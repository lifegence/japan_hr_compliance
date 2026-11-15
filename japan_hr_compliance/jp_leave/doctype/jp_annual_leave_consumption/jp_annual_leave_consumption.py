"""年休充当（LVR-31・33・40・41）。休暇申請の確定・取消でシステムが作成・更新する。"""

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from japan_hr_compliance.jp_leave.utils import GRANT, throw_retention, today_


class JPAnnualLeaveConsumption(Document):
	def on_trash(self):
		from japan_hr_compliance.jp_leave.doctype.jp_annual_leave_grant.jp_annual_leave_grant import retention_until

		grant = frappe.get_doc(GRANT, self.grant) if self.grant and frappe.db.exists(GRANT, self.grant) else None
		if not grant:
			return
		until = retention_until(grant)
		if getdate(today_()) <= until:
			throw_retention(self.doctype, until)
