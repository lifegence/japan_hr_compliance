"""年次有給休暇の会社別設定（LVR-01・20・30・51・53・61）。"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class JPLeaveSettings(Document):
	def validate(self):
		if (self.validity_years or 0) < 2:
			frappe.throw(_("有効期間は法定の2年より短くできません（労基法115条、LVR-30）。"))
		if (self.retention_years or 0) < 3:
			frappe.throw(_("管理簿の保存年数は3年未満にできません（労基則24条の7・経過措置）。"))
		if (self.container_years or 0) < 3:
			frappe.throw(_("器の期間は3年以上にしてください。"))
		if self.grant_method == "斉一的付与" and not self.uniform_base_dates:
			frappe.throw(_("斉一的付与では全社基準日を1つ以上登録してください（LVR-01）。"))
		for row in self.uniform_base_dates:
			try:
				getdate(f"2027-{int(row.base_month):02d}-{int(row.base_day):02d}")
			except Exception:
				frappe.throw(_("全社基準日の行 {0}：{1}月{2}日は日付になりません。").format(row.idx, row.base_month, row.base_day))
		lt = frappe.get_cached_value("Leave Type", self.annual_leave_type, ["is_lwp", "jp_leave_category"], as_dict=True)
		if lt and lt.is_lwp:
			frappe.throw(_("年休の休暇種別は無給（Leave Without Pay）にできません（労基法39条9項、LVR-43）。"))
