"""雇用契約の条件の履歴（従業員マスタ要件 B-1 v1.0 EMP-20〜26）。

1件ごとに確定（Submit）し、訂正は取消＋修正版で行う。任意日付の条件は
japan_hr_compliance.jp_hr_core.contract.get_contract_at で取得する。
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from japan_hr_compliance.jp_hr_core import contract as contract_api
from japan_hr_compliance.jp_hr_core.employee import is_retired


class JPEmploymentContract(Document):
	def validate(self):
		self.set_hours()
		self.validate_conditions()
		self.validate_period()
		self.set_employment_type()
		self.set_change_reason()
		contract_api.validate_chain(self)
		self.check_retroactive_impact()

	def set_change_reason(self):
		"""変更理由（EMP-27）。最初の契約は「入社」とし、2件目以降は入力を必須とする。"""
		if self.change_reason:
			return
		others = frappe.db.exists("JP Employment Contract", {"employee": self.employee, "docstatus": 1, "name": ("!=", self.name),
			"valid_from": ("<", self.valid_from)})
		if others:
			frappe.throw(_("変更理由（更新・条件変更・雇用区分の転換・無期転換等）を選んでください。"))
		self.change_reason = "入社"

	def check_retroactive_impact(self):
		"""既に年休の付与・保険料の控除に使われた期間を遡って変える場合に、影響の一覧を警告する（EMP-27）。"""
		impacts = contract_api.get_retroactive_impact(self)
		self.retro_impact = "\n".join(impacts) if impacts else None
		if impacts:
			frappe.msgprint("<br>".join(frappe.utils.escape_html(x) for x in impacts),
				title=_("遡及変更の警告：既に付与・控除に使われた期間を含みます"), indicator="orange")

	def set_hours(self):
		for prefix in ("weekly_hours", "daily_hours"):
			h, m = cint(self.get(f"{prefix}_h")), cint(self.get(f"{prefix}_m"))
			if h < 0 or not 0 <= m < 60:
				frappe.throw(_("所定労働時間の分は 0〜59 で入力してください。"))
			self.set(prefix, round(h + m / 60, 4) if (h or m) else 0)

	def validate_conditions(self):
		"""比例付与（B-3）と適用判定（B-5）に必要な値が揃っていること（EMP-21）。"""
		if flt(self.weekly_hours) <= 0:
			frappe.throw(_("週所定労働時間を入力してください。"))
		if flt(self.monthly_days) <= 0:
			frappe.throw(_("月所定労働日数を入力してください。"))
		if flt(self.weekly_days) <= 0:
			self.weekly_days = None
			if cint(self.annual_days) <= 0:
				frappe.throw(_("週所定労働日数が定まらない場合は、年間所定労働日数を入力してください。"))
		if cint(self.annual_days) > 0 and not self.annual_days_basis:
			frappe.throw(_("年間所定労働日数の種別（確定／目安）を選んでください。"))
		if flt(self.weekly_days) > 7:
			frappe.throw(_("週所定労働日数は 7 日以下です。"))

	def validate_period(self):
		if not self.contract_start:
			self.contract_start = self.valid_from
		if getdate(self.contract_start) > getdate(self.valid_from):
			frappe.throw(_("契約開始日は適用開始日以前の日付にしてください。"))
		if self.fixed_term:
			if not self.contract_end:
				frappe.throw(_("有期契約は契約終了日を入力してください。"))
			if getdate(self.contract_end) < getdate(self.valid_from):
				frappe.throw(_("契約終了日が適用開始日より前です。"))
		else:
			self.contract_end = None

	def set_employment_type(self):
		"""雇用区分を標準の Employment Type に同期する（EMP-22）。"""
		if self.employment_class:
			if not frappe.db.exists("Employment Type", self.employment_class):
				frappe.get_doc({"doctype": "Employment Type", "employee_type_name": self.employment_class}).insert(ignore_permissions=True)
			self.employment_type = self.employment_class

	def on_submit(self):
		contract_api.refresh_valid_to(self.employee)
		contract_api.sync_employee(self.employee)

	def on_cancel(self):
		contract_api.refresh_valid_to(self.employee)
		contract_api.sync_employee(self.employee)

	def on_trash(self):
		# 確定したことのある契約（取消済みを含む）は電磁的記録として削除させない（EMP-91）
		if self.docstatus != 0:
			frappe.throw(_("確定済み・取消済みの雇用契約は削除できません（電磁的記録の保存）。"), frappe.PermissionError)
		# 退職者の契約履歴は名簿の保存期限まで保持する（EMP-47）
		if is_retired(self.employee):
			frappe.throw(_("退職者の雇用契約は削除できません（労働者名簿の保存のため）。"), frappe.PermissionError)
