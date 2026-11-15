"""手続きの期限規則（従業員マスタ要件 B-1 v1.0 EMP-80）。"""

import frappe
from frappe import _
from frappe.model.document import Document

from japan_hr_compliance.jp_hr_core import procedures


class JPProcedureRule(Document):
	def validate(self):
		if self.due_method != procedures.NO_DUE and (self.due_value or 0) <= 0:
			frappe.throw(_("日数・月数・日を1以上で入力してください。"))
		if self.due_method == procedures.NEXT_MONTH_DAY and self.due_value > 31:
			frappe.throw(_("翌月の日は 1〜31 で入力してください。"))
