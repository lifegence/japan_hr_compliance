"""扶養の異動の記録（従業員マスタ要件 B-1 v1.0 EMP-43）。Employee の扶養親族の表の変更から自動で作成する。"""

import frappe
from frappe import _
from frappe.model.document import Document


class JPDependentChange(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("扶養の異動の記録は変更できません。"), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("扶養の異動の記録は削除できません。"), frappe.PermissionError)
