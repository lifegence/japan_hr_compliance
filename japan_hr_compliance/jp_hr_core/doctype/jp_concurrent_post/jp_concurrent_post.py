"""兼務（JP Concurrent Post）。主務は従業員マスタの部署・役職・上長のまま、兼ねる部署と期間を持つ。"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today


class JPConcurrentPost(Document):
	def validate(self):
		if self.to_date and getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("兼務終了日は兼務開始日以降にしてください"))
		if self.reports_to and self.reports_to == self.employee:
			frappe.throw(_("本人を兼務先での上長にはできません"))
		main = frappe.db.get_value("Employee", self.employee, "department")
		if main and main == self.department:
			frappe.throw(_("主務と同じ部署は兼務にできません"))
		self.check_overlap()
		self.status = status_on(self.from_date, self.to_date)

	def check_overlap(self):
		"""同じ人・同じ部署の兼務は期間が重ならないようにする。"""
		for other in frappe.get_all(
			"JP Concurrent Post",
			filters={"employee": self.employee, "department": self.department, "name": ("!=", self.name or "")},
			fields=["name", "from_date", "to_date"],
		):
			start, end = getdate(self.from_date), getdate(self.to_date) if self.to_date else None
			o_start, o_end = getdate(other.from_date), getdate(other.to_date) if other.to_date else None
			if (end is None or o_start <= end) and (o_end is None or start <= o_end):
				frappe.throw(_("同じ部署の兼務 {0} と期間が重なっています").format(other.name))


def status_on(from_date, to_date, on=None):
	on = getdate(on or today())
	if getdate(from_date) > on:
		return "Scheduled"
	if to_date and getdate(to_date) < on:
		return "Ended"
	return "Active"


def refresh_statuses():
	"""毎日：開始日・終了日を過ぎた兼務の状態を更新する。"""
	for p in frappe.get_all("JP Concurrent Post", fields=["name", "from_date", "to_date", "status"]):
		new = status_on(p.from_date, p.to_date)
		if new != p.status:
			frappe.db.set_value("JP Concurrent Post", p.name, "status", new, update_modified=False)


def active_posts(employee, on=None):
	"""その日に有効な兼務（部署・役職・兼務先での上長）。"""
	on = getdate(on or today())
	return [
		p
		for p in frappe.get_all(
			"JP Concurrent Post",
			filters={"employee": employee, "from_date": ("<=", on)},
			fields=["name", "department", "designation", "reports_to", "from_date", "to_date"],
			order_by="from_date asc",
		)
		if not p.to_date or getdate(p.to_date) >= on
	]
