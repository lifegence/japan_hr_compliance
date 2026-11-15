"""労働条件通知書（従業員マスタ要件 B-1 v1.0 EMP-50〜52）。

確定（Submit）＝交付済。確定時に明示事項の網羅を検査し、電子交付は本人の希望の記録（EMP-36 ④）を必須とし、
交付した版を PDF で添付する。交付後の訂正は取消＋修正版で行い、確定した記録は削除できない（EMP-91）。
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from japan_hr_compliance.jp_hr_core import labor_notice as ln
from japan_hr_compliance.jp_hr_core.consent import LABOR_NOTICE, get_consent_record

PAPER = "書面交付"


class JPLaborConditionNotice(Document):
	def validate(self):
		contract = frappe.get_doc("JP Employment Contract", self.contract)
		if contract.employee != self.employee:
			frappe.throw(_("雇用契約 {0} は従業員 {1} の契約ではありません。").format(self.contract, self.employee))
		if contract.docstatus != 1:
			frappe.throw(_("確定済みの雇用契約から作成してください。"))
		keep_limit = (self.renewal_limit_text or "").strip()
		self.update(ln.contract_values(contract))
		if keep_limit and not self.renewal_limit_text:
			self.renewal_limit_text = keep_limit  # 上限なし等の手入力を残す
		self.part_or_fixed = 1 if ln.is_part_or_fixed(contract) else 0
		if self.part_or_fixed and not (self.explanation_right_text or "").strip():
			self.explanation_right_text = ln.EXPLANATION_RIGHT_TEXT
		if not self.part_or_fixed:
			self.explanation_right_text = None
		self.set_electronic_request()
		missing = ln.missing_items(self)
		self.missing_items = "\n".join(missing)

	def set_electronic_request(self):
		record = get_consent_record(self.employee, LABOR_NOTICE, self.issue_date)
		agreed = bool(record and record.status == "同意")
		self.electronic_request = 1 if agreed else 0
		self.consent_record = record.name if agreed else None

	def before_submit(self):
		if self.missing_items:
			frappe.throw(_("未入力の明示事項があるため交付できません：{0}").format("、".join(self.missing_items.split("\n"))),
				title=_("明示事項の漏れ"))
		if self.method != PAPER and not self.electronic_request:
			frappe.throw(_("電子交付は本人の希望がある場合に限られます（労基則5条4項）。希望の記録がないため交付済にできません。"),
				title=_("電子交付の希望がありません"))

	def on_submit(self):
		self.attach_pdf()

	def attach_pdf(self):
		"""交付した版を書面に出力できる PDF で保存する（EMP-51）。"""
		from frappe.utils.pdf import get_pdf

		html = frappe.render_template("japan_hr_compliance/templates/jp_labor_condition_notice.html", {"doc": self})
		html = f'<html><head><meta charset="utf-8"></head><body>{html}</body></html>'
		pdf = get_pdf(html)
		file = frappe.get_doc({
			"doctype": "File", "file_name": f"{self.name}_{getdate(self.issue_date)}.pdf", "attached_to_doctype": self.doctype,
			"attached_to_name": self.name, "attached_to_field": "pdf", "is_private": 1, "content": pdf,
		})
		file.flags.ignore_permissions = True
		file.insert()
		self.db_set("pdf", file.file_url)

	def on_trash(self):
		if self.docstatus != 0:
			frappe.throw(_("交付した労働条件通知書は削除できません（電磁的記録の保存）。"), frappe.PermissionError)


@frappe.whitelist()
def make_from_contract(contract, notice_type=None):
	"""雇用契約から労働条件通知書の下書きを作る（EMP-50）。"""
	frappe.has_permission("JP Labor Condition Notice", "create", throw=True)
	c = frappe.get_doc("JP Employment Contract", contract)
	if not notice_type:
		notice_type = {"入社": "雇入れ時", "更新": "契約更新時", "無期転換": "無期転換時"}.get(c.change_reason, "労働条件の変更時")
	doc = frappe.get_doc({"doctype": "JP Labor Condition Notice", "employee": c.employee, "contract": c.name,
		"notice_type": notice_type, "issue_date": frappe.utils.today()})
	doc.insert()
	return doc.name
