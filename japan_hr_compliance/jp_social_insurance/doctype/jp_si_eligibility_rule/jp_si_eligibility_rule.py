"""短時間労働者・雇用保険の適用判定条件の版（施行日つき。SIR-71・73）。

給与明細の計算には使わないため、施行日を後から修正できる（SIR-71）。
"""

import frappe
from frappe import _
from frappe.utils import flt

from japan_hr_compliance.jp_social_insurance import masters


class JPSIEligibilityRule(masters.VersionedMaster):
	def validate_values(self):
		if self.wage_requirement and flt(self.wage_threshold) <= 0:
			frappe.throw(_("賃金要件ありの場合は賃金要件の額を入力してください。"))
		if not self.wage_requirement:
			self.wage_threshold = 0
		if flt(self.sh_weekly_hours) <= 0 or flt(self.ei_weekly_hours) <= 0:
			frappe.throw(_("週所定労働時間の基準を入力してください。"))
