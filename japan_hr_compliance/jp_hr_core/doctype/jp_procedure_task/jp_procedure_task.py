"""入退社・扶養異動の手続きタスク（従業員マスタ要件 B-1 v1.0 EMP-80〜82）。期限は procedures.create_tasks で算出する。"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today


class JPProcedureTask(Document):
	def validate(self):
		if self.status == "完了":
			self.completed_on = self.completed_on or today()
			self.completed_by = self.completed_by or frappe.session.user
		else:
			self.completed_on = None
			self.completed_by = None

	def on_trash(self):
		if self.status == "完了":
			frappe.throw(_("完了した手続きの記録は削除できません。"), frappe.PermissionError)
