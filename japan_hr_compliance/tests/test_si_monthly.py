"""月次の社会保険料の給与明細への書き込み（社会保険・雇用保険の計算規則 v1.0 8章 #2〜#4・#12・#15）。

給与明細（Salary Slip）を作り、控除行・事業主負担行・保険料の明細行（JP SI Premium Line）を確かめる。
料率・等級は例示値（tests/si_fixtures.py）。
"""

import frappe

from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.tests import si_fixtures as sf
from japan_hr_compliance.tests.base import JPHRTestCase


class SITestCase(JPHRTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		sf.ensure_all()

	def setUp(self):
		sf.set_settings()


class TestMonthlyPremium(SITestCase):
	def test_basic_premium_6_1(self):
		"""6.1：標準報酬月額 170,000円・45歳。健保＋介護 9,749円、支援金 195円、厚年 15,555円、拠出金は事業主のみ。"""
		emp = sf.make_employee(joining="2026-04-01", birth="1981-05-10", health=170000)
		slip = sf.make_slip(emp.name, "2026-06-01")  # 6月支給 → 5月分（翌月控除）
		self.assertEqual(slip.jp_si_target_months, "2026-05")
		self.assertEqual(sf.deduction(slip, C.K_HEALTH_CARE), 9749)
		self.assertEqual(sf.deduction(slip, C.K_SUPPORT), 195)
		self.assertEqual(sf.deduction(slip, C.K_PENSION), 15555)
		child = sf.lines(slip, C.LINE_MONTHLY, C.P_CHILD)[0]
		self.assertEqual(child.employee_amount, 0)
		self.assertAlmostEqual(child.employer_amount, 612.0)
		hc = sf.lines(slip, C.LINE_MONTHLY, C.P_HEALTH_CARE)[0]
		self.assertAlmostEqual(hc.total_amount, 19499.0)
		self.assertAlmostEqual(hc.employer_amount, 9750.0)
		self.assertEqual(hc.rate_doctype, "JP Health Insurance Rate")
		self.assertTrue(hc.rate_version)
		# 事業主負担行（法定福利費の計上元。SIR-27）
		self.assertAlmostEqual(sf.employer(slip, C.K_ER_CHILD), 612)
		self.assertTrue(slip.net_pay < slip.gross_pay)

	def test_under_40_health_only(self):
		"""6.1：39歳は健保のみ 9.85%。全額 16,745.00、折半 8,372.50 → 8,372円（50銭ちょうどは切捨て）。"""
		emp = sf.make_employee(joining="2026-04-01", birth="1987-05-10", health=170000)
		slip = sf.make_slip(emp.name, "2026-06-01")
		self.assertEqual(sf.deduction(slip, C.K_HEALTH), 8372)
		self.assertEqual(sf.deduction(slip, C.K_HEALTH_CARE), 0)
		self.assertEqual(sf.lines(slip, C.LINE_MONTHLY, C.P_HEALTH)[0].employee_amount, 8372)

	def test_rate_versions_by_target_month_next_month(self):
		"""#2・SIR-01・22・24：翌月控除で、3月支給＝2月分（旧料率）、4月支給＝3月分（新料率・支援金なし）、5月支給＝4月分（支援金あり）。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		mar = sf.make_slip(emp.name, "2026-03-01")
		apr = sf.make_slip(emp.name, "2026-04-01")
		may = sf.make_slip(emp.name, "2026-05-01")
		self.assertEqual(mar.jp_si_target_months, "2026-02")
		self.assertAlmostEqual(sf.lines(mar, C.LINE_MONTHLY, C.P_HEALTH_CARE)[0].rate, 11.5)  # 9.91＋1.59
		self.assertAlmostEqual(sf.lines(apr, C.LINE_MONTHLY, C.P_HEALTH_CARE)[0].rate, 11.47)
		self.assertFalse(sf.lines(apr, C.LINE_MONTHLY, C.P_SUPPORT))
		self.assertTrue(sf.lines(may, C.LINE_MONTHLY, C.P_SUPPORT))
		self.assertEqual(sf.deduction(may, C.K_SUPPORT), 195)

	def test_current_month_deduction(self):
		"""#2・SIR-24：当月控除では4月支給で4月分（支援金あり）を控除する。"""
		sf.set_settings(deduction_timing=C.TIMING_CURRENT)
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		apr = sf.make_slip(emp.name, "2026-04-01")
		self.assertEqual(apr.jp_si_target_months, "2026-04")
		self.assertEqual(sf.deduction(apr, C.K_SUPPORT), 195)

	def test_support_rounded_separately_and_display(self):
		"""#15・SIR-21・22：支援金は健保と別に端数処理。別行表示（介護）でも合算額と一致する。"""
		sf.set_settings(care_display=C.DISPLAY_SEPARATE)
		emp = sf.make_employee(joining="2026-04-01", birth="1981-05-10", health=170000)
		slip = sf.make_slip(emp.name, "2026-06-01")
		health, care = sf.deduction(slip, C.K_HEALTH), sf.deduction(slip, C.K_CARE)
		self.assertEqual(health, 8372)
		self.assertEqual(health + care, 9749)
		self.assertEqual(sf.deduction(slip, C.K_SUPPORT), 195)
		sf.set_settings(support_display=C.DISPLAY_COMBINED)
		slip2 = sf.make_slip(emp.name, "2026-07-01")
		self.assertEqual(sf.deduction(slip2, C.K_HEALTH_CARE), 9749 + 195)
		self.assertEqual(sf.deduction(slip2, C.K_SUPPORT), 0)

	def test_draft_version_not_used(self):
		"""SIR-06：下書きの版は計算に使われない。"""
		frappe.get_doc({"doctype": "JP Health Insurance Rate", "effective_from": "2026-06-01", "status": C.STATUS_DRAFT,
			"rates": [{"prefecture": sf.TOKYO, "health_rate": 15, "care_rate": 2, "support_rate": 1}]}).insert()
		emp = sf.make_employee(joining="2026-04-01", birth="1981-05-10", health=170000)
		slip = sf.make_slip(emp.name, "2026-07-01")  # 6月分
		self.assertAlmostEqual(sf.lines(slip, C.LINE_MONTHLY, C.P_HEALTH_CARE)[0].rate, 11.47)
		self.assertIn("下書き", slip.jp_si_warnings)


class TestAgeAttainment(SITestCase):
	def _care(self, birth, pay_month):
		emp = sf.make_employee(joining="2025-04-01", birth=birth, health=170000)
		slip = sf.make_slip(emp.name, pay_month)
		return bool(sf.lines(slip, C.LINE_MONTHLY, C.P_HEALTH_CARE))

	def test_care_40_birthday_1st_and_2nd(self):
		"""#3・6.2：1986/11/1 生まれは10月分（11月支給）から介護、1986/11/2 生まれは11月分（12月支給）から。"""
		self.assertTrue(self._care("1986-11-01", "2026-11-01"))
		self.assertFalse(self._care("1986-11-02", "2026-11-01"))
		self.assertTrue(self._care("1986-11-02", "2026-12-01"))

	def test_care_65(self):
		"""#3・6.2：1961/11/1 生まれは9月分（10月支給）が最後、1961/11/2 生まれは10月分（11月支給）が最後。"""
		self.assertTrue(self._care("1961-11-01", "2026-10-01"))
		self.assertFalse(self._care("1961-11-01", "2026-11-01"))
		self.assertTrue(self._care("1961-11-02", "2026-11-01"))
		self.assertFalse(self._care("1961-11-02", "2026-12-01"))

	def test_pension_70_and_child_contribution(self):
		"""#3・SIR-65：70歳到達月（1956/11/2 生まれ → 2026年11月）から厚年・拠出金なし。健保は続く。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1956-11-02", health=170000)
		oct_ = sf.make_slip(emp.name, "2026-11-01")  # 10月分
		nov = sf.make_slip(emp.name, "2026-12-01")  # 11月分
		self.assertTrue(sf.lines(oct_, C.LINE_MONTHLY, C.P_PENSION))
		self.assertFalse(sf.lines(nov, C.LINE_MONTHLY, C.P_PENSION))
		self.assertFalse(sf.lines(nov, C.LINE_MONTHLY, C.P_CHILD))
		self.assertTrue(sf.lines(nov, C.LINE_MONTHLY, C.P_HEALTH))

	def test_health_75(self):
		"""#3：75歳は誕生日当日に健保喪失。1951/11/15 生まれは10月分まで健保。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1951-11-15", health=170000, pension=0)
		oct_ = sf.make_slip(emp.name, "2026-11-01")
		nov = sf.make_slip(emp.name, "2026-12-01")
		self.assertTrue(sf.lines(oct_, C.LINE_MONTHLY, C.P_HEALTH))
		self.assertFalse(sf.lines(nov, C.LINE_MONTHLY, C.P_HEALTH))


class TestRetirement(SITestCase):
	def test_month_end_retirement_two_months(self):
		"""#4・6.3・SIR-26：12/31 退職は12月支給の最終給与で11月分＋12月分。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000, relieving="2026-12-31")
		slip = sf.make_slip(emp.name, "2026-12-01")
		self.assertEqual(slip.jp_si_target_months, "2026-11, 2026-12")
		self.assertEqual(sf.deduction(slip, C.K_PENSION), 15555 * 2)

	def test_day_before_month_end(self):
		"""#4・6.3：12/30 退職は11月分のみ。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000, relieving="2026-12-30")
		slip = sf.make_slip(emp.name, "2026-12-01", end_date="2026-12-30")
		self.assertEqual(slip.jp_si_target_months, "2026-11")
		self.assertEqual(sf.deduction(slip, C.K_PENSION), 15555)

	def test_cancel_two_months_requires_method(self):
		"""SIR-26：2か月控除を取り消すと12月分は控除されず、確定には徴収方法の記録が必要。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000, relieving="2026-12-31")
		slip = sf.make_slip(emp.name, "2026-12-01", jp_si_cancel_two_months=1)
		self.assertEqual(slip.jp_si_target_months, "2026-11")
		self.assertEqual(slip.jp_si_deferred_months, "2026-12")
		with self.assertRaises(frappe.ValidationError):
			slip.submit()
		slip.reload()
		slip.jp_si_deferred_method = C.DEFERRED_TRANSFER
		slip.save()
		slip.submit()
		self.assertEqual(slip.docstatus, 1)

	def test_two_months_with_revision(self):
		"""#12：退職月が随時改定の適用月の場合、11月分と12月分を別の標準報酬月額で計算する。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000, relieving="2026-12-31")
		sf.add_history(emp.name, "2026-12-01", 240000, 240000, decision=C.DECISION_OCCASIONAL)
		slip = sf.make_slip(emp.name, "2026-12-01")
		pension = sf.lines(slip, C.LINE_MONTHLY, C.P_PENSION)
		self.assertEqual([l.base_amount for l in pension], [170000, 240000])
		self.assertEqual(sf.deduction(slip, C.K_PENSION), 15555 + 21960)

	def test_same_month_gain_loss(self):
		"""#4・6.4：11/10 入社・11/25 退職、当月末払いの翌月控除の会社で、11月支給の最終給与で11月分を控除する。"""
		emp = sf.make_employee(joining="2026-11-10", birth="1981-05-10", health=170000, relieving="2026-11-25")
		slip = sf.make_slip(emp.name, "2026-11-01", posting_day=30, end_date="2026-11-25")
		self.assertEqual(slip.jp_si_target_months, "2026-11")
		self.assertEqual(sf.deduction(slip, C.K_PENSION), 15555)

	def test_manual_override(self):
		"""SIR-26：手動変更をオンにした明細は再計算されず、修正した金額が残る。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		slip = sf.make_slip(emp.name, "2026-06-01")
		slip.jp_si_manual = 1
		for row in slip.deductions:
			if row.salary_component == C.K_PENSION:
				row.amount = 10000
		slip.save()
		self.assertEqual(sf.deduction(slip, C.K_PENSION), 10000)


class TestPaymentBaseDays(SITestCase):
	def _attendance(self, employee, day, status, leave_type=None):
		frappe.get_doc({"doctype": "Attendance", "employee": employee, "attendance_date": day, "status": status,
			"company": sf.fx.COMPANY, "leave_type": leave_type}).insert(ignore_permissions=True).submit()

	def test_monthly_calendar_days(self):
		"""SIR-41：月給制で欠勤控除がなければ計算期間の暦日数。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		self.assertEqual(sf.make_slip(emp.name, "2026-05-01").jp_payment_base_days, 31)

	def test_monthly_with_absence_deduction(self):
		"""SIR-41：欠勤控除を行う月給制は所定労働日数−その計算期間の欠勤日数（控除月によらない）。"""
		sf.set_settings(absence_deduction_monthly=1, prescribed_days_basis=C.PRESCRIBED_CONTRACT)
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		for day in ("2026-05-11", "2026-05-12"):
			self._attendance(emp.name, day, "Absent")
		slip = sf.make_slip(emp.name, "2026-05-01")
		self.assertEqual(slip.jp_absence_days, 2)
		self.assertEqual(slip.jp_payment_base_days, 19)  # 契約の月所定労働日数 21 − 2

	def test_hourly_attendance_days(self):
		"""SIR-41：時給制は出勤日数（有給休暇を含む）。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000, contract={"wage_type": "時給"})
		for d in range(1, 19):
			self._attendance(emp.name, f"2026-05-{d:02d}", "Present")
		self.assertEqual(sf.make_slip(emp.name, "2026-05-01").jp_payment_base_days, 18)
