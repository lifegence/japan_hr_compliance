"""健保の標準賞与額の年度累計（SIR-31・33）。前職（協会けんぽ）の累計は手入力。"""

from frappe.model.document import Document
from frappe.utils import flt


class JPBonusAccumulation(Document):
	def autoname(self):
		self.name = f"{self.employee}-{self.fiscal_year}"

	def validate(self):
		self.own_amount = sum(flt(e.health_standard_bonus) for e in self.entries)
		self.total_amount = flt(self.own_amount) + flt(self.prior_employer_amount)
