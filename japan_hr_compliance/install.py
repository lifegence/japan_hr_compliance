"""インストール・マイグレート時の処理。"""

import frappe

from japan_hr_compliance.jp_social_insurance import setup as si_setup
from japan_hr_compliance.setup import custom_fields, roles

# 雇用区分（EMP-22）。標準の Employment Type に同じ名前で作り、契約から同期する
EMPLOYMENT_TYPES = ("正社員", "契約社員", "パートタイマー", "アルバイト", "嘱託", "その他", "日雇い")

# 変更履歴（Version）を残す DocType（EMP-87）。本アプリの DocType は JSON で track_changes を有効にしている
TRACKED_DOCTYPES = ("Employee",)


def before_install():
	roles.ensure_roles()


def before_migrate():
	if frappe.db.table_exists("Role"):
		roles.ensure_roles()


def after_install():
	after_migrate()


def after_migrate():
	roles.ensure_roles()
	custom_fields.setup()
	roles.apply_employee_permissions()
	ensure_employment_types()
	ensure_track_changes()
	si_setup.after_migrate()
	ensure_p1_masters()


def ensure_p1_masters():
	"""B-1 P1 の初期マスタ：手続きの期限規則（EMP-80）とマイナンバーの書類の保存期限（EMP-66）。既存の行は上書きしない。"""
	from japan_hr_compliance.jp_hr_core import my_number, procedures

	procedures.ensure_rules()
	my_number.ensure_retention_rules()


def ensure_employment_types():
	for name in EMPLOYMENT_TYPES:
		if not frappe.db.exists("Employment Type", name):
			frappe.get_doc({"doctype": "Employment Type", "employee_type_name": name}).insert(ignore_permissions=True)


def ensure_track_changes():
	for doctype in TRACKED_DOCTYPES:
		if not frappe.get_meta(doctype).track_changes:
			frappe.make_property_setter(
				{"doctype": doctype, "doctype_or_field": "DocType", "property": "track_changes", "value": 1, "property_type": "Check"},
				validate_fields_for_doctype=False,
			)
