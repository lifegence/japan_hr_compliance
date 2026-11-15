"""マイナンバーを記載した書類の保存期限（従業員マスタ要件 B-1 v1.0 EMP-66）。"""

import frappe
from frappe import _
from frappe.model.document import Document


class JPMyNumberRetentionRule(Document):
	def validate(self):
		if (self.years or 0) <= 0:
			frappe.throw(_("保存年数は1以上にしてください。"))
