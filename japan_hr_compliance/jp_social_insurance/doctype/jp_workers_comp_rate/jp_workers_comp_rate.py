"""労災保険率（業種ごと・適用開始日つきの版。SIR-05・64）。

JP Workplace の労災保険の事業の種類（wc_business_type）からリンクする。版は子テーブルの行で、
行ごとに状態・出典・確認者を持つ。給与明細で使われた行は変更・削除できない（SIR-06）。
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import masters

PROTECTED = ("effective_from", "rate_permille")


def row_used(row_name):
	return bool(frappe.db.exists(masters.PREMIUM_LINE, {"rate_doctype": "JP Workers Comp Rate", "rate_row": row_name,
		"parenttype": "Salary Slip", "docstatus": 1}))


class JPWorkersCompRate(Document):
	def validate(self):
		confirmed = {}
		for row in self.rates:
			if not 0 <= flt(row.rate_permille) < 1000:
				frappe.throw(_("行 {0}：労災保険率は1000分率で入力してください。").format(row.idx))
			masters.validate_confirmation(row, prefix=_("行 {0}：").format(row.idx))
			if row.status == C.STATUS_CONFIRMED:
				key = getdate(row.effective_from)
				if key in confirmed:
					frappe.throw(_("行 {0} と行 {1}：同じ適用開始日の確認済の版があります。").format(confirmed[key], row.idx))
				confirmed[key] = row.idx
		before = self.get_doc_before_save()
		if not before:
			return
		current = {r.name: r for r in self.rates}
		for old in before.rates:
			if not row_used(old.name):
				continue
			new = current.get(old.name)
			if new is None:
				frappe.throw(_("適用開始日 {0} の版は給与明細で使われたため削除できません。").format(old.effective_from))
			for field in PROTECTED:
				if str(new.get(field)) != str(old.get(field)) and not (
					field == "rate_permille" and flt(new.get(field)) == flt(old.get(field))
				):
					frappe.throw(_("適用開始日 {0} の版は給与明細で使われたため変更できません。訂正版の行を追加してください。")
						.format(old.effective_from), title=_("使用済みの版"))
			if old.status == C.STATUS_CONFIRMED and new.status == C.STATUS_DRAFT:
				frappe.throw(_("給与明細で使われた版は下書きに戻せません。"))

	def on_trash(self):
		if any(row_used(r.name) for r in self.rates):
			frappe.throw(_("給与明細で使われた版があるため削除できません。"))
