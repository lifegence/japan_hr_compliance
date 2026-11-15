"""会社の厚年被保険者数（短時間労働者を除く）の月ごとの記録（SIR-71）。"""

from frappe.model.document import Document
from frappe.utils import getdate


class JPCompanyHeadcount(Document):
	def autoname(self):
		self.name = f"{self.company}-{getdate(self.month).strftime('%Y-%m')}"

	def validate(self):
		self.month = getdate(self.month).replace(day=1)
		self.at_or_above = 1 if self.threshold and (self.pension_insured_count or 0) >= self.threshold else 0
