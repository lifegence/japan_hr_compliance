"""賞与の上限の版（SIR-31・32）。"""

import frappe
from frappe import _
from frappe.utils import flt

from japan_hr_compliance.jp_social_insurance import masters


class JPBonusCap(masters.VersionedMaster):
	def validate_values(self):
		if flt(self.health_annual_cap) <= 0 or flt(self.pension_monthly_cap) <= 0:
			frappe.throw(_("上限額を入力してください。"))
