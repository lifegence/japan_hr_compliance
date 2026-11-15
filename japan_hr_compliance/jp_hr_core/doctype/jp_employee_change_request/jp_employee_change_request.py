"""従業員の変更申請（従業員マスタ要件 B-1 v1.0 EMP-86）。処理は japan_hr_compliance.jp_hr_core.change_request。"""

import frappe
from frappe import _
from frappe.model.document import Document


class JPEmployeeChangeRequest(Document):
	def validate(self):
		if self.is_new() and not self.flags.jp_api_insert:
			frappe.throw(_("変更申請は従業員ポータルから作成してください。"), frappe.PermissionError)
		if not self.is_new() and not self.flags.jp_decision:
			frappe.throw(_("変更申請の内容は変更できません。承認または却下してください。"), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("変更申請の記録は削除できません。"), frappe.PermissionError)
