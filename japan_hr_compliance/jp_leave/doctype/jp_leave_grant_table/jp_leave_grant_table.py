"""就業規則ごとの付与テーブル（LVR-02・03）。

保存時に法定の表と照合し、法定日数を下回る箇所があれば警告する（保存は止めない）。警告はコメントとして記録する。
"""

import frappe
from frappe import _
from frappe.model.document import Document

from japan_hr_compliance.jp_leave.calc.grant_table import (
	GrantTable,
	GrantTableRow,
	WorkCategory,
	find_below_statutory,
	legal_days,
)
from japan_hr_compliance.jp_leave.utils import D, GRANT_TABLE


class JPLeaveGrantTable(Document):
	def validate(self):
		seen = set()
		for row in self.rows:
			key = (row.service_months, row.category)
			if key in seen:
				frappe.throw(_("行 {0}：勤続{1}か月・{2}の行が重複しています。").format(row.idx, row.service_months, row.category))
			seen.add(key)
			if row.days is None or row.days < 0:
				frappe.throw(_("行 {0}：付与日数は 0 以上にしてください。").format(row.idx))
			row.legal_days = float(legal_days(row.service_months, WorkCategory(row.category)))
			row.below_legal = 1 if row.days < row.legal_days else 0
		warnings = find_below_statutory(self.to_calc())
		self.below_statutory = 1 if warnings else 0
		self.warning_text = "\n".join(w.message for w in warnings) or None
		if warnings:
			frappe.msgprint("<br>".join(w.message for w in warnings), title=_("法定日数を下回る設定"), indicator="orange")

	def on_update(self):
		if self.below_statutory and (self.has_value_changed("warning_text") or self.flags.in_insert):
			self.add_comment("Comment", _("法定との照合の警告（{0}）：{1}").format(frappe.session.user, self.warning_text))

	def to_calc(self):
		return GrantTable(
			name=self.table_name,
			rows=[GrantTableRow(r.service_months, WorkCategory(r.category), D(r.days)) for r in self.rows],
			work_rule=self.work_rule_class or None,
		)


def get_tables(company=None):
	"""就業規則の区分 → 付与テーブル（calc.GrantTable）。会社指定のテーブルを優先する（LVR-04）。"""
	tables = {}
	rows = frappe.get_all(GRANT_TABLE, filters={"work_rule_class": ("is", "set")}, fields=["name", "work_rule_class", "company"],
		order_by="company asc")
	for r in rows:
		if r.company and r.company != company:
			continue
		if r.work_rule_class in tables and not r.company:
			continue
		tables[r.work_rule_class] = frappe.get_doc(GRANT_TABLE, r.name).to_calc()
	return tables
