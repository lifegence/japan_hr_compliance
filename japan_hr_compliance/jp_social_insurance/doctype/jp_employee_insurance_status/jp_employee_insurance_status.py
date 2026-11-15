"""被保険者の資格履歴（健保・厚年・雇保。SIR-11）。

同じ保険の期間は重ならないこと。保存のたびに、保険ごとの最新の期間を Employee の
資格取得日・喪失日（B-1 EMP-11）に反映する（B-1 の項目を参照する他機能と揃えるため）。
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from japan_hr_compliance.jp_social_insurance import constants as C


class JPEmployeeInsuranceStatus(Document):
	def validate(self):
		if self.lost and getdate(self.lost) <= getdate(self.acquired):
			frappe.throw(_("資格喪失日は資格取得日より後の日付にしてください。"))
		others = frappe.get_all(self.doctype, filters={"employee": self.employee, "insurance_type": self.insurance_type,
			"name": ("!=", self.name)}, fields=["name", "acquired", "lost"])
		start, end = getdate(self.acquired), getdate(self.lost) if self.lost else getdate("9999-12-31")
		for o in others:
			o_end = getdate(o.lost) if o.lost else getdate("9999-12-31")
			if getdate(o.acquired) < end and start < o_end:
				frappe.throw(_("同じ保険の資格期間 {0} と重なっています。").format(o.name), title=_("資格期間の重複"))

	def on_update(self):
		sync_employee_qualification(self.employee, self.insurance_type)

	def on_trash(self):
		self.flags.deleting = True

	def after_delete(self):
		sync_employee_qualification(self.employee, self.insurance_type)


def sync_employee_qualification(employee, insurance_type):
	latest = frappe.get_all("JP Employee Insurance Status", filters={"employee": employee, "insurance_type": insurance_type},
		fields=["acquired", "lost"], order_by="acquired desc", limit=1)
	acquired_field, lost_field = C.B1_QUALIFICATION_FIELDS[insurance_type]
	values = {acquired_field: latest[0].acquired, lost_field: latest[0].lost} if latest else {acquired_field: None, lost_field: None}
	frappe.db.set_value("Employee", employee, values, update_modified=False)
