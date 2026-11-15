"""標準報酬月額の履歴（SIR-12・46・47）。

金額で保持し、等級番号は適用開始月の等級表から引いて表示する（5.2）。確定（Submit）した行だけが
保険料計算に使われる。保険者算定・休業終了時改定の結果は決定区分を付けて手入力で登録できる（SIR-47）。
同じ従業員・同じ適用開始月の確定済みの行は1件に限る。
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import masters


class JPStandardRemunerationHistory(Document):
	def validate(self):
		self.effective_from = masters.month_start(self.effective_from)
		if not flt(self.health_standard) and not flt(self.pension_standard):
			frappe.throw(_("健保または厚年の標準報酬月額を入力してください。"))
		self.health_grade = self._grade(C.TABLE_HEALTH, self.health_standard)
		self.pension_grade = self._grade(C.TABLE_PENSION, self.pension_standard)

	def _grade(self, kind, standard):
		if not flt(standard):
			return None
		try:
			table = masters.select_grade_table(kind, self.effective_from)
		except LookupError:
			frappe.throw(_("{0} に有効な確認済の{1}の等級表がありません。").format(self.effective_from, kind))
		try:
			return table.version.by_standard(masters.dec(standard)).grade
		except LookupError:
			frappe.throw(_("{0}の標準報酬月額 {1:,.0f} 円は等級表 {2} にありません。").format(kind, flt(standard), table.name))

	def before_submit(self):
		dup = frappe.db.get_value(self.doctype, {"employee": self.employee, "effective_from": self.effective_from,
			"docstatus": 1, "name": ("!=", self.name)}, "name")
		if dup:
			frappe.throw(_("同じ適用開始月（{0}）の確定済みの標準報酬月額 {1} があります。訂正する場合は {1} を取り消してください。")
				.format(getdate(self.effective_from), dup), title=_("標準報酬月額の重複"))


@frappe.whitelist()
def propose_acquisition_standard(employee, effective_from, remuneration=None):
	"""資格取得時の標準報酬月額の提案（SIR-12）。

	報酬月額の見込みが渡されなければ、給与構造の割当（Salary Structure Assignment）の base を
	見込みとして使う（B-1 の雇用契約に報酬額の項目がないため。PR に記載の仮の扱い）。
	"""
	frappe.has_permission("Employee", "read", employee, throw=True)
	if not remuneration:
		remuneration = frappe.db.get_value("Salary Structure Assignment",
			{"employee": employee, "docstatus": 1, "from_date": ("<=", getdate(effective_from))}, "base",
			order_by="from_date desc")
	if not remuneration:
		return None
	value = masters.dec(remuneration)
	out = {"remuneration": float(value), "decision_type": C.DECISION_ACQUISITION}
	for kind, field in ((C.TABLE_HEALTH, "health"), (C.TABLE_PENSION, "pension")):
		try:
			grade = masters.select_grade_table(kind, effective_from).version.lookup(value)
			out[f"{field}_standard"] = float(grade.standard)
			out[f"{field}_grade"] = grade.grade
		except LookupError:
			pass
	return out
