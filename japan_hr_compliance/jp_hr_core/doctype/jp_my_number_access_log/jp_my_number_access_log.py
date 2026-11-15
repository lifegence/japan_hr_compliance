"""マイナンバーのアクセスログ（従業員マスタ要件 B-1 v1.0 EMP-64）。追記のみ。すべてのロールで編集・削除できない。"""

import frappe
from frappe import _
from frappe.model.document import Document


class JPMyNumberAccessLog(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("アクセスログは変更できません。"), frappe.PermissionError)
		if not self.flags.jp_log_insert:
			frappe.throw(_("アクセスログは手で作成できません。"), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("アクセスログは削除できません。"), frappe.PermissionError)
