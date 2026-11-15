"""対象者一括の付与（斉一的付与・前倒し、LVR-21）と計画的付与の登録（LVR-41）。

確定（Submit）で対象者ごとに年休付与または休暇申請（取得区分「計画的付与」）を作成・確定する。
対象者ごとの成否は結果欄に記録し、失敗した対象者は個別の画面で対応する。
"""

import frappe
from frappe import _
from frappe.model.document import Document

from japan_hr_compliance.jp_leave.utils import GRANT, annual_leave_type_for


class JPLeaveBulkEntry(Document):
	def validate(self):
		seen = set()
		for row in self.employees:
			if row.employee in seen:
				frappe.throw(_("対象者 {0} が重複しています。").format(row.employee))
			seen.add(row.employee)
			if frappe.db.get_value("Employee", row.employee, "company") != self.company:
				frappe.throw(_("対象者 {0} は会社 {1} の従業員ではありません。").format(row.employee, self.company))

	def on_submit(self):
		for row in self.employees:
			frappe.db.savepoint("jp_bulk_row")
			try:
				name = self.make_grant(row) if self.purpose == "付与" else self.make_planned_leave(row)
				row.db_set({"reference": name, "result": _("作成・確定しました")})
			except Exception as e:
				frappe.db.rollback(save_point="jp_bulk_row")
				frappe.clear_last_message()
				row.db_set({"reference": None, "result": _("エラー：{0}").format(frappe.utils.strip_html(str(e)))})

	def make_grant(self, row):
		doc = frappe.get_doc({
			"doctype": GRANT, "employee": row.employee, "grant_date": self.grant_date, "is_manual": 1,
			"days": row.days or self.days or 0, "grant_kind": self.grant_kind if self.grant_kind == "調整" else "法定どおり",
			"round_no": self.round_no or None, "reason": _("{0}（一括入力 {1}）").format(self.reason, self.name),
			"manual_checked": self.manual_checked,
		})
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()
		return doc.name

	def make_planned_leave(self, row):
		la = frappe.get_doc({
			"doctype": "Leave Application", "employee": row.employee, "company": self.company,
			"leave_type": annual_leave_type_for(self.company), "from_date": self.from_date, "to_date": self.to_date,
			"jp_take_kind": "計画的付与", "status": "Approved",
			"leave_approver": frappe.db.get_value("Employee", row.employee, "leave_approver"),
			"description": _("計画的付与（労基法39条6項。{0}）：{1}").format(self.name, self.reason),
		})
		la.flags.ignore_permissions = True
		la.insert()
		la.submit()
		return la.name
