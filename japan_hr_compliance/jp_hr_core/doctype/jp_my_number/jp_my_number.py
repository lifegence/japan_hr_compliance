"""マイナンバー（従業員マスタ要件 B-1 v1.0 EMP-60〜67）。処理の本体は japan_hr_compliance.jp_hr_core.my_number。

番号の項目（my_number）は Password 型。controller の validate の時点では入力された平文が入っており、
validate の後に Frappe が暗号化して __Auth に保存し、項目の値を「*」に置き換えてから DB に書く。
そのため検証はここで行い、エラーの文言に番号を含めない。
"""

import frappe
from frappe import _
from frappe.model.document import Document

from japan_hr_compliance.jp_hr_core import my_number as mn


class JPMyNumber(Document):
	def validate(self):
		self.validate_owner()
		self.validate_number()
		self.set_verification_status()
		self.set_disposal()
		self.masked_number = mn.DISPOSED_LABEL if self.status == mn.DISPOSED else (mn.MASK if self.my_number else mn.UNREGISTERED_LABEL)

	def validate_owner(self):
		if self.owner_type == "本人":
			self.dependent_row = None
			self.dependent_name = None
		else:
			row = frappe.db.get_value("JP Employee Dependent", {"name": self.dependent_row, "parent": self.employee,
				"parenttype": "Employee"}, ["dependent_name"], as_dict=True) if self.dependent_row else None
			if row:
				self.dependent_name = row.dependent_name
			elif self.is_new():
				frappe.throw(_("扶養親族の番号は、従業員の扶養親族の表の行を指定してください。"))
		filters = {"employee": self.employee, "owner_type": self.owner_type, "status": mn.ACTIVE, "name": ("!=", self.name)}
		if self.owner_type == "扶養親族":
			filters["dependent_row"] = self.dependent_row
		if self.status == mn.ACTIVE and frappe.db.exists(mn.DOCTYPE, filters):
			frappe.throw(_("この{0}の有効な番号はすでに登録されています。").format(self.owner_type))

	def validate_number(self):
		value = self.my_number
		self.flags.jp_number_changed = False
		if self.flags.jp_disposing:
			return
		if self.status == mn.DISPOSED:
			if value and not mn.is_dummy(value):
				frappe.throw(_("廃棄済みの記録に番号は登録できません。新しい記録を作成してください。"))
			return
		if not value:
			if not self.is_new() and frappe.db.get_value(mn.DOCTYPE, self.name, mn.FIELD):
				frappe.throw(_("番号を空にすることはできません。番号を消す場合は廃棄の手続きを行ってください。"))
			if self.is_new():
				frappe.throw(_("個人番号を入力してください。"))
			return
		if mn.is_dummy(value):
			return
		if not self.flags.jp_system_update:
			mn._require_officer(action="登録・変更")
		try:
			self.my_number = mn.normalize_number(value)
		except ValueError as e:
			frappe.throw(str(e), title=_("個人番号の誤り"))
		self.flags.jp_number_changed = True

	def set_verification_status(self):
		ok = all((self.number_check_doc, self.identity_check_doc, self.verified_on, self.verified_by))
		self.verification_status = "確認済" if ok else "未確認"

	def set_disposal(self):
		if self.status == mn.DISPOSED:
			return
		self.disposal_due, self.disposal_basis = mn.compute_disposal(self.owner_type, mn.derived_work_end(self))

	def after_insert(self):
		mn.write_log(self, "登録", "登録", _("番号の登録"))
		self.flags.jp_number_changed = False

	def on_update(self):
		if self.flags.jp_number_changed and not self.flags.in_insert:
			mn.write_log(self, "変更", "変更", _("番号の変更"))

	def on_trash(self):
		frappe.throw(_("マイナンバーの記録は削除できません。保存期限の経過後に廃棄の手続きを行ってください。"), frappe.PermissionError)
