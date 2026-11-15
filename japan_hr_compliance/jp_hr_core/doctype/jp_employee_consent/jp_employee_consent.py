"""口座振込の同意・電子交付の承諾とその撤回の記録（従業員マスタ要件 B-1 v1.0 EMP-35〜37）。

記録は追記のみ。保存後の変更と削除はできない（撤回は「撤回」の記録を加える）。
"""

import frappe
from frappe import _
from frappe.model.document import Document

from japan_hr_compliance.jp_hr_core import consent


class JPEmployeeConsent(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("同意・承諾の記録は変更できません。撤回する場合は「撤回」の記録を追加してください。"), frappe.PermissionError)
		self.user = frappe.session.user
		self.ip_address = getattr(frappe.local, "request_ip", None)
		if self.method == "書面" and not self.attachment:
			# 人事・給与担当が書面で取得した同意を代理登録する場合は、書面の写しを添付する（EMP-38）
			frappe.throw(_("書面で取得した同意・承諾は、書面の写しを添付してください。"))
		if not self.text_version and self.method == "Web":
			self.text_version = consent.CONSENT_TEXTS[self.consent_type][0]
		current = consent.has_consent(self.employee, self.consent_type, self.consent_datetime)
		if self.status == consent.WITHDRAW and not current:
			frappe.throw(_("{0} は同意・承諾されていないため撤回できません。").format(self.consent_type))
		if self.status == consent.AGREE and self.consent_type == consent.BANK_TRANSFER:
			snapshot = consent.accounts_snapshot(self.employee)
			if not snapshot:
				frappe.throw(_("口座振込の同意には振込口座の登録が必要です。"))
			self.accounts_snapshot = snapshot

	def on_trash(self):
		frappe.throw(_("同意・承諾の記録は削除できません。"), frappe.PermissionError)
