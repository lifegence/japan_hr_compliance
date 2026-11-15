"""事業場マスタ（従業員マスタ要件 B-1 v1.0 EMP-30）。"""

from frappe.model.document import Document

from japan_hr_compliance.jp_hr_core.workplace import validate_insurance_units


class JPWorkplace(Document):
	def validate(self):
		validate_insurance_units(self)
