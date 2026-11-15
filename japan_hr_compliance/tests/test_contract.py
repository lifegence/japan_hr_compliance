"""雇用契約履歴のテスト（B-1 v1.0 EMP-20〜26・32、6章 観点 #1・#2・#4）。"""

import frappe
from frappe.utils import add_days, getdate, today

from japan_hr_compliance.jp_hr_core.contract import get_contract_at, notify_fixed_term_ending
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase


class TestEmploymentContract(JPHRTestCase):
	def test_condition_change_day_before_and_on(self):
		"""#1 週5日・40h → 2027-04-01 に週3日・24h。3/31 と 4/1 で異なる条件が返る（EMP-26）。"""
		emp = fx.make_employee(joining="2025-04-01")
		fx.make_contract(emp.name, "2025-04-01")
		fx.make_contract(emp.name, "2027-04-01", employment_class="パートタイマー", work_rule_class="パートタイマー用",
			wage_type="時給", weekly_hours_h=24, weekly_days=3, daily_hours_h=8, monthly_days=13, change_reason="雇用区分の転換")
		before = get_contract_at(emp.name, "2027-03-31")
		after = get_contract_at(emp.name, "2027-04-01")
		self.assertEqual((before.weekly_days, before.weekly_hours), (5, 40))
		self.assertEqual((after.weekly_days, after.weekly_hours), (3, 24))
		self.assertEqual(after.work_rule_class, "パートタイマー用")
		# 適用終了日は次の契約の前日に自動で入る
		self.assertEqual(getdate(before.valid_to), getdate("2027-03-31"))
		self.assertIsNone(after.valid_to)

	def test_outside_employment_returns_none(self):
		"""入社前・退職後の日付では空を返す（EMP-26）。退職日当日は契約を返す。"""
		emp = fx.make_employee(joining="2025-04-01")
		fx.make_contract(emp.name, "2025-04-01")
		self.assertIsNone(get_contract_at(emp.name, "2025-03-31"))
		emp.reload()
		emp.relieving_date = "2026-03-31"
		emp.jp_separation_type = "自己都合"
		emp.save()
		self.assertIsNotNone(get_contract_at(emp.name, "2026-03-31"))
		self.assertIsNone(get_contract_at(emp.name, "2026-04-01"))
		# 最後の契約の適用終了日は退職日
		self.assertEqual(getdate(get_contract_at(emp.name, "2026-03-31").valid_to), getdate("2026-03-31"))

	def test_overlap_is_rejected(self):
		"""#2 同じ適用開始日の契約（重複）は保存時エラー（EMP-20）。"""
		emp = fx.make_employee(joining="2025-04-01")
		fx.make_contract(emp.name, "2025-04-01")
		with self.assertRaises(frappe.ValidationError):
			fx.make_contract(emp.name, "2025-04-01", submit=False)

	def test_gap_from_joining_is_rejected(self):
		"""#2 入社日より後に始まる最初の契約（入社日からの空白）は保存時エラー。"""
		emp = fx.make_employee(joining="2025-04-01")
		with self.assertRaises(frappe.ValidationError):
			fx.make_contract(emp.name, "2025-05-01", submit=False)

	def test_gap_between_fixed_term_contracts_is_rejected(self):
		"""#2 有期契約の終了日の翌日より後に次の契約が始まる（契約の間の空白）は保存時エラー。"""
		emp = fx.make_employee(joining="2025-04-01")
		fx.make_contract(emp.name, "2025-04-01", employment_class="契約社員", fixed_term=1, contract_end="2026-03-31",
			expected_period="2か月を超える見込み", renewal="更新する場合がある")
		with self.assertRaises(frappe.ValidationError):
			fx.make_contract(emp.name, "2026-05-01", submit=False)
		# 翌日から始まる更新後の契約は保存できる
		fx.make_contract(emp.name, "2026-04-01", employment_class="契約社員", fixed_term=1, contract_end="2027-03-31",
			expected_period="2か月を超える見込み", change_reason="更新")

	def test_cancel_and_amend(self):
		"""訂正は取消＋修正版で行う（EMP-20）。修正版が同じ適用開始日で確定できる。"""
		emp = fx.make_employee(joining="2025-04-01")
		first = fx.make_contract(emp.name, "2025-04-01")
		first.cancel()
		amended = frappe.copy_doc(first)
		amended.docstatus = 0
		amended.amended_from = first.name
		amended.weekly_hours_h, amended.weekly_hours_m = 37, 30
		amended.insert()
		amended.submit()
		self.assertEqual(get_contract_at(emp.name, "2025-06-01").weekly_hours, 37.5)
		self.assertEqual(get_contract_at(emp.name, "2025-06-01").name, amended.name)

	def test_annual_days_required_without_weekly_days(self):
		"""#4 週所定日数が定まらない者は年間所定日数が必須。種別（目安）が B-3 に渡る（EMP-21）。"""
		emp = fx.make_employee(joining="2025-04-01")
		with self.assertRaises(frappe.ValidationError):
			fx.make_contract(emp.name, "2025-04-01", submit=False, weekly_days=None, employment_class="アルバイト",
				wage_type="時給", weekly_hours_h=10)
		with self.assertRaises(frappe.ValidationError):
			fx.make_contract(emp.name, "2025-04-01", submit=False, weekly_days=None, annual_days=96)
		fx.make_contract(emp.name, "2025-04-01", weekly_days=None, annual_days=96, annual_days_basis="目安",
			employment_class="アルバイト", wage_type="時給", weekly_hours_h=10)
		c = get_contract_at(emp.name, "2025-10-01")
		self.assertFalse(c.weekly_days)
		self.assertEqual((c.annual_days, c.annual_days_basis), (96, "目安"))

	def test_hours_and_minutes(self):
		emp = fx.make_employee(joining="2025-04-01")
		c = fx.make_contract(emp.name, "2025-04-01", weekly_hours_h=38, weekly_hours_m=45, daily_hours_h=7, daily_hours_m=45)
		self.assertEqual(c.weekly_hours, 38.75)
		self.assertEqual(c.daily_hours, 7.75)
		with self.assertRaises(frappe.ValidationError):
			fx.make_contract(emp.name, "2025-05-01", submit=False, weekly_hours_m=60)

	def test_employment_type_and_workplace_sync(self):
		"""雇用区分を employment_type に、事業場・部署を branch・internal_work_history に同期する（EMP-22・32）。"""
		emp = fx.make_employee(joining="2025-04-01")
		fx.make_contract(emp.name, "2025-04-01")
		fx.make_contract(emp.name, add_days(today(), -1), workplace=fx.WORKPLACE_FACTORY, change_reason="異動")
		emp.reload()
		self.assertEqual(emp.employment_type, "正社員")
		self.assertEqual(emp.branch, "テスト工場")
		self.assertEqual(emp.internal_work_history[-1].branch, "テスト工場")

	def test_fixed_term_end_notice(self):
		"""有期契約の終了日の30日前に人事担当へ通知する（EMP-23）。"""
		user = fx.ensure_user("jp-hr-notice@example.com", ["JP HR User"])
		start = add_days(today(), -60)
		emp = fx.make_employee(joining=start)
		c = fx.make_contract(emp.name, start, employment_class="契約社員", fixed_term=1, contract_end=add_days(today(), 30),
			expected_period="2か月を超える見込み")
		self.assertIn(c.name, notify_fixed_term_ending())
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": user, "document_name": c.name}))

	def test_track_changes(self):
		"""契約の変更履歴が残る（EMP-87）。"""
		emp = fx.make_employee(joining="2025-04-01")
		c = fx.make_contract(emp.name, "2025-04-01", submit=False)
		c.weekly_hours_h = 39
		c.save(ignore_version=False)  # テスト中は既定で Version を作らないため明示する
		self.assertTrue(frappe.db.exists("Version", {"ref_doctype": "JP Employment Contract", "docname": c.name}))
