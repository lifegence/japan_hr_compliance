"""Employee の日本向け項目のテスト（B-1 v1.0 EMP-01・03・05・06・10・11・40〜42・44・47、6章 観点 #5・#8・#13）。"""

import frappe

from japan_hr_compliance.jp_hr_core.address import count_by_lg_code, get_address_at
from japan_hr_compliance.jp_hr_core.dependents import age_on, get_dependent_incomes, get_hi_dependents_at
from japan_hr_compliance.jp_hr_core.normalize import to_zengin_kana, validate_lg_code
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase

ADDR_OLD = {"valid_from": "2025-04-01", "postal_code": "1700013", "prefecture": "東京都", "municipality": "豊島区",
	"lg_code": "131164", "address_line": "東池袋9-9-9（架空）"}
ADDR_NEW = {"valid_from": "2027-01-20", "postal_code": "160-0023", "prefecture": "東京都", "municipality": "新宿区",
	"lg_code": "131041", "address_line": "西新宿9-9-9（架空）", "building": "テストハイツ101"}


class TestEmployeeMaster(JPHRTestCase):
	def test_kana_normalized_to_zenkaku(self):
		"""半角カナ・ひらがなは全角カナに変換して保存する（EMP-01）。"""
		emp = fx.make_employee(jp_last_name_kana="ｼｹﾝ", jp_first_name_kana="じろう")
		self.assertEqual((emp.jp_last_name_kana, emp.jp_first_name_kana), ("シケン", "ジロウ"))
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(jp_last_name_kana="試験")

	def test_kana_is_mandatory(self):
		with self.assertRaises(frappe.MandatoryError):
			fx.make_employee(jp_first_name_kana=None)

	def test_employee_number_unique_in_company(self):
		"""社員番号は同一会社内で重複不可（EMP-03）。"""
		fx.make_employee(employee_number="T-0001")
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(employee_number="T-0001")

	def test_insurance_numbers(self):
		"""番号の形式検証と先頭ゼロの保持（EMP-10）、資格取得日・喪失日（EMP-11）。"""
		emp = fx.make_employee(jp_basic_pension_no="1234-567890", jp_emp_ins_no="1234-567890-1", jp_hi_insurer_no="01130012",
			jp_hi_symbol="０１２３４５６７", jp_hi_number="007", jp_hi_branch_no="00", jp_hi_acquired="2025-04-01",
			jp_pi_acquired="2025-04-01", jp_ei_acquired="2025-04-01")
		self.assertEqual(emp.jp_basic_pension_no, "1234567890")
		self.assertEqual(emp.jp_emp_ins_no, "12345678901")
		self.assertEqual(emp.jp_hi_symbol, "01234567")
		self.assertEqual(emp.jp_hi_number, "007")
		for field, value in (("jp_basic_pension_no", "123456789"), ("jp_emp_ins_no", "1234-567890"), ("jp_hi_insurer_no", "1234567"),
				("jp_hi_symbol", "12A4")):
			with self.assertRaises(frappe.ValidationError, msg=field):
				fx.make_employee(**{field: value})
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(jp_hi_acquired="2025-04-01", jp_hi_lost="2025-03-31")

	def test_insurance_fields_are_level2_and_not_printed(self):
		meta = frappe.get_meta("Employee")
		for field in ("jp_basic_pension_no", "jp_emp_ins_no", "jp_hi_symbol", "jp_hi_acquired", "jp_dependents"):
			self.assertEqual(meta.get_field(field).permlevel, 2, field)
		self.assertTrue(meta.get_field("jp_basic_pension_no").print_hide)
		self.assertEqual(meta.get_field("jp_bank_accounts").permlevel, 1)

	def test_address_on_january_first(self):
		"""#5 1月中に転居した従業員の1月1日時点の住所は転居前（EMP-06）。"""
		emp = fx.make_employee(jp_addresses=[ADDR_NEW, ADDR_OLD])
		self.assertEqual(emp.jp_addresses[0].lg_code, "131164")  # 適用開始日の順に並べ替える
		self.assertEqual(emp.jp_addresses[0].postal_code, "170-0013")
		jan1 = get_address_at(emp.name, "2027-01-01")
		self.assertEqual((jan1.municipality, jan1.lg_code), ("豊島区", "131164"))
		self.assertEqual(get_address_at(emp.name, "2027-01-20").lg_code, "131041")
		self.assertIn("テストハイツ101", get_address_at(emp.name, "2027-02-01").full_address)
		self.assertIsNone(get_address_at(emp.name, "2025-03-31"))

	def test_lg_code_check_digit_and_count(self):
		"""市区町村コードは検査数字を検証し、コードで集計できる（EMP-05）。"""
		self.assertEqual(validate_lg_code("011002"), "011002")
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(jp_addresses=[dict(ADDR_OLD, lg_code="131165")])
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(jp_addresses=[ADDR_OLD, dict(ADDR_NEW, valid_from="2025-04-01")])
		fx.make_employee(jp_addresses=[ADDR_OLD])
		self.assertGreaterEqual(count_by_lg_code("2026-01-01", fx.COMPANY).get("131164", 0), 1)

	def test_dependents(self):
		"""扶養親族の項目・任意日付の年齢（EMP-40）、年分の所得見積（EMP-41）、被扶養者の期間（EMP-42）。"""
		emp = fx.make_employee(
			jp_dependents=[
				{"dependent_name": "試験 花子", "dependent_kana": "ｼｹﾝ ﾊﾅｺ", "relation": "配偶者", "birth_date": "1991-02-03",
					"gender": "女", "cohabiting": 1, "hi_dependent_from": "2025-04-01", "nenkin_3go": 1},
				{"dependent_name": "試験 一郎", "relation": "子", "birth_date": "2010-04-02", "cohabiting": 1,
					"disability_class": "同居特別障害者", "hi_dependent_from": "2025-04-01", "hi_dependent_to": "2026-03-31"},
			],
			jp_dependent_incomes=[
				{"dependent_name": "試験 花子", "tax_year": 2026, "estimated_income": 400000, "tax_class": "源泉控除対象配偶者"},
				{"dependent_name": "試験 花子", "tax_year": 2027, "estimated_income": 1500000, "tax_class": "対象外"},
			],
		)
		self.assertEqual(emp.jp_dependents[0].dependent_kana, "シケン　ハナコ")
		self.assertEqual(age_on("2010-04-02", "2026-04-01"), 15)
		self.assertEqual(age_on("2010-04-02", "2026-04-02"), 16)
		self.assertEqual(len(get_hi_dependents_at(emp.name, "2026-03-31")), 2)
		self.assertEqual([d.dependent_name for d in get_hi_dependents_at(emp.name, "2026-04-01")], ["試験 花子"])
		self.assertEqual(get_dependent_incomes(emp.name, 2027)[0].tax_class, "対象外")
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(jp_dependents=[], jp_dependent_incomes=[{"dependent_name": "存在しない", "tax_year": 2026}])

	def test_dismissal_and_death_require_reason(self):
		"""#8 解雇は理由、死亡は年月日と原因が必須（EMP-44）。"""
		def retire(emp, **values):
			doc = frappe.get_doc("Employee", emp.name)  # 失敗した保存の後は読み直す
			doc.update(values)
			doc.save()
			return doc

		emp = fx.make_employee()
		with self.assertRaises(frappe.ValidationError):
			retire(emp, relieving_date="2026-03-31")  # 区分なし
		with self.assertRaises(frappe.ValidationError):
			retire(emp, relieving_date="2026-03-31", jp_separation_type="解雇")
		retire(emp, relieving_date="2026-03-31", jp_separation_type="解雇", jp_dismissal_reason="就業規則第○条に基づく（架空）")
		emp2 = fx.make_employee()
		death = dict(relieving_date="2026-05-10", jp_separation_type="死亡", jp_death_date="2026-05-10")
		with self.assertRaises(frappe.ValidationError):
			retire(emp2, **death)
		retire(emp2, jp_death_cause="病気（架空）", **death)

	def test_retired_employee_cannot_be_deleted(self):
		"""#13 退職者の Employee・契約・扶養は管理者でも削除できない（EMP-47）。"""
		emp = fx.make_employee(jp_dependents=[{"dependent_name": "試験 花子", "relation": "配偶者", "birth_date": "1991-02-03"}])
		draft = fx.make_contract(emp.name, "2025-04-01", submit=False)
		emp.reload()
		emp.status = "Left"
		emp.relieving_date = "2026-03-31"
		emp.jp_separation_type = "自己都合"
		emp.save()
		self.assertEqual(frappe.session.user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Employee", emp.name)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("JP Employment Contract", draft.name)
		emp.reload()
		emp.jp_dependents = []
		with self.assertRaises(frappe.ValidationError):
			emp.save()

	def test_active_employee_can_be_deleted(self):
		emp = fx.make_employee()
		frappe.delete_doc("Employee", emp.name)
		self.assertFalse(frappe.db.exists("Employee", emp.name))

	def test_zengin_kana(self):
		self.assertEqual(to_zengin_kana("シケン　タロウ"), "ｼｹﾝ ﾀﾛｳ")
		self.assertEqual(to_zengin_kana("ｷｬﾛﾙ"), "ｷﾔﾛﾙ")
