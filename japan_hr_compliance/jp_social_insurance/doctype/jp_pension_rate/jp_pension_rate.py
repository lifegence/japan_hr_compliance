"""厚生年金保険料率・子ども・子育て拠出金率の版（SIR-01・06、5.3）。料率は % で保持する。"""

import frappe
from frappe import _
from frappe.utils import flt

from japan_hr_compliance.jp_social_insurance import calc, masters
from japan_hr_compliance.jp_social_insurance import constants as C


class JPPensionRate(masters.VersionedMaster):
	def validate_values(self):
		for field in ("pension_rate", "child_contribution_rate"):
			if not 0 <= flt(self.get(field)) < 100:
				frappe.throw(_("料率は 0 以上 100 未満の % で入力してください。"))
		if flt(self.pension_rate) <= 0:
			frappe.throw(_("厚生年金保険料率を入力してください。"))

	@frappe.whitelist()
	def reconcile_premium_table(self, file_url=None, content=None):
		"""保険料額表の厚年の折半額と全等級を突合する（CSV の列：grade,pension_half）。"""
		frappe.has_permission(self.doctype, "write", self.name, throw=True)
		rows = masters.read_csv(content if content is not None else masters.file_content(file_url))
		table = masters.select_grade_table(C.TABLE_PENSION, self.effective_from, include_draft=True)
		expected = {}
		for i, row in enumerate(rows, start=2):
			value = masters._num(row, "pension_half", i)
			if value is not None:
				expected[int(masters._num(row, "grade", i))] = value
		mismatches = calc.reconcile_premium_table(table.version, masters.pct(self.pension_rate), expected)
		masters.append_reconciliation(self, "厚生年金", "全国", table.name, mismatches, len(table.version.grades))
		self.save()
		return len(mismatches)
