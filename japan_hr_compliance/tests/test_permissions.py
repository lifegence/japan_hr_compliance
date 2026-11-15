"""既定ロールと権限レベルのテスト（B-1 v1.0 EMP-85、5章の対応表）。"""

import frappe

from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase

USERS = {
	"jp-hr@example.com": ["JP HR User"],
	"jp-payroll@example.com": ["JP Payroll User"],
	"jp-mynumber@example.com": ["JP My Number Officer"],
	"jp-self@example.com": ["Employee"],
}

# user -> (read levels, write levels) on Employee
EXPECTED = {
	"jp-hr@example.com": ({0, 1, 2}, {0, 2}),
	"jp-payroll@example.com": ({0, 1, 2}, {1}),
	"jp-mynumber@example.com": ({0, 2}, set()),
	"jp-self@example.com": ({0, 1, 2}, set()),
}


class TestPermissions(JPHRTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for email, roles in USERS.items():
			fx.ensure_user(email, roles)
		# Employee ロールは従業員に紐づかないユーザーからは外される（ERPNext の validate_employee_role）
		if not frappe.db.exists("Employee", {"user_id": "jp-self@example.com"}):
			fx.make_employee(user_id="jp-self@example.com")
		frappe.get_doc("User", "jp-self@example.com").add_roles("Employee")

	def test_roles_exist(self):
		for role in ("JP HR User", "JP Payroll User", "JP My Number Officer"):
			self.assertTrue(frappe.db.exists("Role", role))

	def test_employee_permlevel_matrix(self):
		meta = frappe.get_meta("Employee")
		for user, (read, write) in EXPECTED.items():
			self.assertEqual(set(meta.get_permlevel_access("read", user=user)), read, user)
			self.assertEqual(set(meta.get_permlevel_access("write", user=user)), write, user)

	def test_contract_and_consent_permissions(self):
		def can(user, doctype, ptype):
			return frappe.has_permission(doctype, ptype, user=user)

		self.assertTrue(can("jp-hr@example.com", "JP Employment Contract", "submit"))
		self.assertFalse(can("jp-payroll@example.com", "JP Employment Contract", "write"))
		self.assertTrue(can("jp-payroll@example.com", "JP Employment Contract", "read"))
		self.assertTrue(can("jp-payroll@example.com", "JP Employee Consent", "create"))
		self.assertFalse(can("jp-hr@example.com", "JP Employee Consent", "create"))
		self.assertTrue(can("jp-hr@example.com", "JP Employee Consent", "read"))
		self.assertFalse(can("jp-mynumber@example.com", "JP Employee Consent", "read"))
		self.assertTrue(can("jp-hr@example.com", "JP Workplace", "write"))
		self.assertFalse(can("jp-payroll@example.com", "JP Workplace", "write"))

	def test_p1_doctype_permissions(self):
		"""B-1 P1 の DocType の既定の権限（EMP-63〜65・50・80・86）。"""
		def can(user, doctype, ptype):
			return frappe.has_permission(doctype, ptype, user=user)

		# マイナンバー：登録・変更は事務取扱担当者のみ。削除・エクスポート・レポート・印刷は誰もできない
		self.assertTrue(can("jp-mynumber@example.com", "JP My Number", "create"))
		self.assertFalse(can("jp-hr@example.com", "JP My Number", "create"))
		self.assertTrue(can("jp-hr@example.com", "JP My Number", "read"))
		for user in USERS:
			for ptype in ("delete", "export", "report", "print"):
				self.assertFalse(can(user, "JP My Number", ptype), f"{user} {ptype}")
		self.assertEqual(set(frappe.get_meta("JP My Number").get_permlevel_access("read", user="jp-mynumber@example.com")), {0, 1})
		self.assertEqual(set(frappe.get_meta("JP My Number").get_permlevel_access("read", user="jp-hr@example.com")), {0})
		self.assertTrue(can("jp-mynumber@example.com", "JP My Number Access Log", "read"))
		self.assertFalse(can("jp-hr@example.com", "JP My Number Access Log", "read"))
		# 労働条件通知書：人事担当が作成・交付、給与担当は閲覧
		self.assertTrue(can("jp-hr@example.com", "JP Labor Condition Notice", "submit"))
		self.assertFalse(can("jp-payroll@example.com", "JP Labor Condition Notice", "write"))
		# 手続きタスク：人事担当・給与担当が更新できる
		self.assertTrue(can("jp-hr@example.com", "JP Procedure Task", "write"))
		self.assertTrue(can("jp-payroll@example.com", "JP Procedure Task", "write"))
		# 変更申請：画面からは作成しない（ポータルの API から作る）
		self.assertFalse(can("jp-hr@example.com", "JP Employee Change Request", "create"))
		self.assertTrue(can("jp-self@example.com", "JP Employee Change Request", "read"))
