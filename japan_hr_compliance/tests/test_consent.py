"""振込口座と同意記録のテスト（B-1 v1.0 EMP-34〜37、6章 観点 #6・#7）。"""

import frappe

from japan_hr_compliance.jp_hr_core import consent
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase

ACCOUNTS = [
	{"priority": 1, "bank_code": "0001", "bank_name": "テスト銀行", "branch_code": "001", "branch_name": "本店", "account_type": "普通",
		"account_no": "123456", "holder_kana": "シケン　タロウ", "fixed_amount": 50000},
	{"priority": 2, "bank_code": "9900", "bank_name": "テスト貯金", "branch_code": "018", "account_type": "普通",
		"account_no": "7654321", "holder_kana": "ｼｹﾝ ﾀﾛｳ"},
]


class TestBankAndConsent(JPHRTestCase):
	def make_portal_employee(self):
		email = fx.ensure_user(f"jp-emp-{frappe.generate_hash(length=6)}@example.com", ["Employee"])
		return fx.make_employee(user_id=email, jp_bank_accounts=ACCOUNTS), email

	def test_bank_account_normalization_and_rules(self):
		"""口座の正規化と分割の規則（EMP-34）。"""
		emp = fx.make_employee(jp_bank_accounts=ACCOUNTS)
		self.assertEqual(emp.jp_bank_accounts[0].account_no, "0123456")
		self.assertEqual(emp.jp_bank_accounts[0].holder_kana, "ｼｹﾝ ﾀﾛｳ")
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(jp_bank_accounts=[ACCOUNTS[0]])  # 残額の口座がない
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(jp_bank_accounts=[dict(ACCOUNTS[0], priority=2), dict(ACCOUNTS[1], priority=1)])  # 残額が最後でない
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(jp_bank_accounts=[dict(ACCOUNTS[1], bank_code="01")])

	def test_split_and_cash_without_consent(self):
		"""#6 口座2件（定額＋残額）の分割。同意なしは振込対象外で現金払いの一覧に出る（EMP-34・35）。"""
		with_consent = fx.make_employee(jp_bank_accounts=ACCOUNTS)
		without = fx.make_employee(jp_bank_accounts=ACCOUNTS)
		consent.record_consent(with_consent.name, consent.BANK_TRANSFER, method="書面", wage_scope="月例給与の全額",
			attachment="/private/files/consent-test.pdf")
		allocation = consent.get_transfer_allocation(with_consent.name, 230000)
		self.assertEqual([(a.bank_code, a.amount) for a in allocation], [("0001", 50000), ("9900", 180000)])
		self.assertEqual([a.amount for a in consent.get_transfer_allocation(with_consent.name, 30000)], [30000])
		self.assertIsNone(consent.get_transfer_allocation(without.name, 230000))
		split = consent.split_by_transfer_consent([with_consent.name, without.name])
		self.assertEqual(split, {"transfer": [with_consent.name], "cash": [without.name]})
		# 同意のない従業員を口座振込にすると保存時エラー
		without.reload()
		without.salary_mode = "Bank"
		with self.assertRaises(frappe.ValidationError):
			without.save()
		with_consent.reload()
		with_consent.salary_mode = "Bank"
		with_consent.save()

	def test_portal_individual_consents_and_withdrawal(self):
		"""#7 ポータルで3項目に同意 → 明細の電子交付のみ撤回。同意記録3件と撤回1件、書面交付の一覧に入る（EMP-36・37）。"""
		emp, email = self.make_portal_employee()
		frappe.set_user(email)
		created = consent.submit_portal_consents(
			{consent.PAYSLIP: "同意", consent.BANK_TRANSFER: "同意", consent.LABOR_NOTICE: "同意", consent.WITHHOLDING_SLIP: ""},
			wage_scope="月例給与と賞与の全額", start_period="2027年4月支給分から")
		self.assertEqual(len(created), 3)
		records = frappe.get_all("JP Employee Consent", filters={"employee": emp.name}, fields=["consent_type", "status", "text_version", "user"])
		self.assertEqual({r.consent_type for r in records}, {consent.PAYSLIP, consent.BANK_TRANSFER, consent.LABOR_NOTICE})
		self.assertTrue(all(r.text_version == "2026-11-v1" and r.user == email for r in records))
		self.assertIsNone(consent.get_consent_status(emp.name)[consent.WITHHOLDING_SLIP])  # 選ばなかった項目は記録しない
		bank = frappe.get_doc("JP Employee Consent", {"employee": emp.name, "consent_type": consent.BANK_TRANSFER})
		self.assertIn("0123456", bank.accounts_snapshot)
		frappe.set_user("Administrator")
		self.assertNotIn(emp.name, consent.get_paper_delivery_employees(fx.COMPANY))

		frappe.set_user(email)
		self.assertEqual(len(consent.submit_portal_consents({consent.PAYSLIP: "撤回"})), 1)
		frappe.set_user("Administrator")
		statuses = frappe.get_all("JP Employee Consent", filters={"employee": emp.name}, pluck="status")
		self.assertEqual((statuses.count("同意"), statuses.count("撤回")), (3, 1))
		self.assertIn(emp.name, consent.get_paper_delivery_employees(fx.COMPANY))
		self.assertTrue(consent.has_consent(emp.name, consent.BANK_TRANSFER))

	def test_bank_transfer_consent_requires_wage_scope_on_portal(self):
		emp, email = self.make_portal_employee()
		frappe.set_user(email)
		with self.assertRaises(frappe.ValidationError):
			consent.submit_portal_consents({consent.BANK_TRANSFER: "同意"})

	def test_consent_records_are_append_only(self):
		"""同意記録は変更・削除できない。撤回は同意済みのものだけ（EMP-37）。"""
		emp = fx.make_employee(jp_bank_accounts=ACCOUNTS)
		with self.assertRaises(frappe.ValidationError):
			consent.record_consent(emp.name, consent.PAYSLIP, consent.WITHDRAW)
		doc = consent.record_consent(emp.name, consent.PAYSLIP)
		doc.method = "書面"
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("JP Employee Consent", doc.name)

	def test_bank_transfer_consent_requires_accounts(self):
		emp = fx.make_employee()
		with self.assertRaises(frappe.ValidationError):
			consent.record_consent(emp.name, consent.BANK_TRANSFER)

	def test_portal_page_lists_each_item_separately(self):
		"""同意画面は4項目を個別に示し、文面の版を表示する（EMP-36）。"""
		from frappe.website.serve import get_response_content

		from japan_hr_compliance.www import jp_consent

		emp, email = self.make_portal_employee()
		frappe.set_user(email)
		context = jp_consent.get_context(frappe._dict())
		self.assertEqual([i.consent_type for i in context["items"]], list(consent.CONSENT_TYPES))
		self.assertTrue(all(i.version for i in context["items"]))
		html = get_response_content("jp_consent")
		self.assertIn("口座振込", html)
		self.assertNotIn("すべてに同意", html)
