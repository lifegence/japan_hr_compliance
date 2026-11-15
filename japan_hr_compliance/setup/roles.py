"""日本向けの既定ロールと Employee の権限レベル別の権限（従業員マスタ要件 B-1 v1.0 EMP-85、5章の対応表）。

| 項目群（権限レベル）       | 人事担当 | 給与担当 | 事務取扱担当者 | 一般従業員（本人のみ） |
|----------------------------|----------|----------|----------------|------------------------|
| 基本情報・住所・名簿（0） | 編集     | 閲覧     | 閲覧           | 閲覧                   |
| 口座（1）                  | 閲覧     | 編集     | —              | 閲覧                   |
| 扶養・保険番号（2）        | 編集     | 閲覧     | 閲覧           | 閲覧                   |

一般従業員（Employee ロール）が本人の分だけを見られるのは、Employee の user_id に基づく
ユーザー権限（create_user_permission）による。標準の動きを変えない。
マイナンバー（EMP-60〜67）の権限は JP My Number の DocType 側で持つ（番号の入力欄は権限レベル1で事務取扱担当者のみ）。
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

HR_USER = "JP HR User"
PAYROLL_USER = "JP Payroll User"
MY_NUMBER_OFFICER = "JP My Number Officer"
JP_ROLES = (HR_USER, PAYROLL_USER, MY_NUMBER_OFFICER)

READ, EDIT = "read", "edit"
EDIT_RIGHTS = ("write", "create", "report", "export", "print", "email", "share")
READ_RIGHTS = ("report", "print")

# role -> {permlevel: READ | EDIT}
EMPLOYEE_MATRIX = {
	HR_USER: {0: EDIT, 1: READ, 2: EDIT},
	PAYROLL_USER: {0: READ, 1: EDIT, 2: READ},
	MY_NUMBER_OFFICER: {0: READ, 2: READ},
	"Employee": {1: READ, 2: READ},
	"Employee Self Service": {1: READ, 2: READ},
	# 標準の人事ロールが追加項目を見られなくならないようにする
	"HR User": {1: READ, 2: EDIT},
	"HR Manager": {1: EDIT, 2: EDIT},
}


def ensure_roles():
	for role in JP_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def apply_employee_permissions():
	"""Employee の Custom DocPerm を対応表どおりにする。既存の行は権限だけ揃える。"""
	for role, levels in EMPLOYEE_MATRIX.items():
		for level, mode in levels.items():
			if not frappe.db.exists("Custom DocPerm", {"parent": "Employee", "role": role, "permlevel": level, "if_owner": 0}):
				add_permission("Employee", role, level)
			update_permission_property("Employee", role, level, "read", 1, validate=False)
			if level == 0:
				for right in EDIT_RIGHTS:
					value = 1 if mode == EDIT or right in READ_RIGHTS else 0
					update_permission_property("Employee", role, level, right, value, validate=False)
			else:
				# 権限レベル1以上は read / write だけが意味を持つ
				update_permission_property("Employee", role, level, "write", 1 if mode == EDIT else 0, validate=False)
	frappe.clear_cache(doctype="Employee")


def setup():
	ensure_roles()
	apply_employee_permissions()
