"""通常の労働者の所定労働条件（従業員マスタ要件 B-1 v1.0 EMP-29）。"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class JPStandardWorkerCondition(Document):
	def validate(self):
		if self.job_category and not self.workplace:
			frappe.throw(_("業務区分ごとの条件は事業場を指定してください（事業場が空欄の行は会社の既定値です）。"))
		if self.workplace and frappe.db.get_value("JP Workplace", self.workplace, "company") != self.company:
			frappe.throw(_("事業場 {0} は会社 {1} に属していません。").format(self.workplace, self.company))
		if flt(self.weekly_hours) <= 0 or flt(self.monthly_days) <= 0:
			frappe.throw(_("週所定労働時間と月所定労働日数を入力してください。"))
		filters = {"company": self.company, "workplace": self.workplace or ("is", "not set"),
			"job_category": self.job_category or ("is", "not set"), "valid_from": self.valid_from, "name": ("!=", self.name)}
		if frappe.db.exists("JP Standard Worker Condition", filters):
			frappe.throw(_("同じ会社・事業場・業務区分・適用開始日の条件が既にあります。"))
