"""社保側で追加していた Employee.jp_officer_type（役員区分）を、B-1 の jp_si_exempt_reason に統合する。

- 既存の値は、被保険者でない場合の理由が空欄の従業員に限って引き継ぐ（入力済みの理由は上書きしない）
- 引継ぎ後に Custom Field を削除する
"""
import frappe

MAPPING = {
	"役員": "役員（雇用保険の対象外）",
	"兼務役員": "兼務役員（雇保は労働者部分で判定）",
}


def execute():
	if not frappe.db.has_column("Employee", "jp_officer_type"):
		return
	if not frappe.db.has_column("Employee", "jp_si_exempt_reason"):
		# 引継ぎ先の列は after_migrate（custom_fields.setup）で作られるため、post_model_sync の時点では
		# まだない場合がある。先に Custom Field を作ってから引き継ぐ
		from japan_hr_compliance.setup import custom_fields

		custom_fields.setup()
	rows = frappe.get_all("Employee", filters={"jp_officer_type": ("in", list(MAPPING))},
		fields=["name", "jp_officer_type", "jp_si_exempt_reason"])
	for row in rows:
		if row.jp_si_exempt_reason:
			if row.jp_si_exempt_reason != MAPPING[row.jp_officer_type]:
				frappe.log_error(
					title="役員区分の統合：既存の理由を優先",
					message=f"{row.name}: 役員区分={row.jp_officer_type}、被保険者でない場合の理由={row.jp_si_exempt_reason}",
				)
			continue
		frappe.db.set_value("Employee", row.name, "jp_si_exempt_reason", MAPPING[row.jp_officer_type],
			update_modified=False)
	if frappe.db.exists("Custom Field", "Employee-jp_officer_type"):
		frappe.delete_doc("Custom Field", "Employee-jp_officer_type", ignore_permissions=True, force=True)
