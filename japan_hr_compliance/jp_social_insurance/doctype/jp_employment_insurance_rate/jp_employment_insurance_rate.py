"""雇用保険料率の版（年度・事業の種類ごと。SIR-04・60）。料率は1000分率で保持する。"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from japan_hr_compliance.jp_social_insurance import masters


class JPEmploymentInsuranceRate(masters.VersionedMaster):
	def validate_values(self):
		d = getdate(self.effective_from)
		self.fiscal_year = d.year if d.month >= 4 else d.year - 1
		for field in ("employee_permille", "employer_permille"):
			if not 0 <= flt(self.get(field)) < 1000:
				frappe.throw(_("料率は 0 以上 1000 未満の1000分率で入力してください。"))
