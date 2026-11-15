"""標準報酬月額の等級表の版（SIR-03・45）。健保・厚年を別に持ち、等級数は固定しない。"""

import frappe
from frappe import _
from frappe.utils import flt

from japan_hr_compliance.jp_social_insurance import masters


class JPStandardRemunerationTable(masters.VersionedMaster):
	def validate_values(self):
		self.grades = sorted(self.grades, key=lambda g: g.grade)
		for i, g in enumerate(self.grades, start=1):
			g.idx = i
		if self.grades:
			# 下限は前の等級の上限と一致させる（空欄なら補う）
			for prev, cur in zip(self.grades, self.grades[1:]):
				if not flt(cur.lower) and flt(prev.upper):
					cur.lower = prev.upper
			try:
				masters.build_grade_table(self, any_status=True)
			except ValueError as e:
				frappe.throw(_("等級表が不正です：{0}").format(e))

	@frappe.whitelist()
	def import_grades_csv(self, file_url=None, content=None):
		"""CSV（grade,standard,lower,upper。最上位の upper は空欄）で等級を置き換える。"""
		frappe.has_permission(self.doctype, "write", self.name, throw=True)
		rows = masters.read_csv(content if content is not None else masters.file_content(file_url))
		self.set("grades", [])
		for i, row in enumerate(rows, start=2):
			upper = masters._num(row, "upper", i)
			self.append("grades", {"grade": int(masters._num(row, "grade", i)),
				"standard": float(masters._num(row, "standard", i)),
				"lower": float(masters._num(row, "lower", i) or 0),
				"upper": float(upper) if upper is not None else None})
		self.save()
		return len(self.grades)
