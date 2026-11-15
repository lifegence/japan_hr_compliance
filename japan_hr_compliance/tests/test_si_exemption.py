"""産前産後休業・育児休業等の保険料免除（社会保険・雇用保険の計算規則 v1.0 8章 #8・#14、6.6、SIR-50〜52）。

休暇申請（Leave Application）と勤怠（Attendance）から免除月を判定し、給与明細の保険料に反映する。
"""

from datetime import date

import frappe

from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import exemption
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests import si_fixtures as sf
from japan_hr_compliance.tests.test_si_monthly import SITestCase


def leave(employee, leave_type, from_date, to_date):
	doc = frappe.get_doc({"doctype": "Leave Application", "employee": employee, "leave_type": leave_type,
		"from_date": from_date, "to_date": to_date, "company": fx.COMPANY, "status": "Approved",
		"posting_date": from_date, "follow_via_email": 0})
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def months(employee):
	return [m for m in exemption.exemption_months(employee)]


class TestExemption(SITestCase):
	def emp(self):
		return sf.make_employee(joining="2025-04-01", birth="1991-05-10", health=300000, gender="Female").name

	def test_case_a_14_days(self):
		"""育休A：11/5〜11/18（14日）は11月が免除。"""
		e = self.emp()
		leave(e, sf.LEAVE_CHILDCARE, "2026-11-05", "2026-11-18")
		self.assertEqual(months(e), [date(2026, 11, 1)])

	def test_case_b_13_days(self):
		"""育休B：11/5〜11/17（13日）は免除なし。"""
		e = self.emp()
		leave(e, sf.LEAVE_CHILDCARE, "2026-11-05", "2026-11-17")
		self.assertEqual(months(e), [])

	def test_case_c_across_months(self):
		"""育休C：11/20〜12/15 は11月のみ（12月は開始月が異なるため14日ルールの対象外）。"""
		e = self.emp()
		leave(e, sf.LEAVE_CHILDCARE, "2026-11-20", "2026-12-15")
		self.assertEqual(months(e), [date(2026, 11, 1)])

	def test_case_e_maternity_no_14_day_rule(self):
		"""産休E：産後休業が12/30 に終了すると12月は免除されない（11月まで）。"""
		e = self.emp()
		leave(e, sf.LEAVE_MATERNITY, "2026-10-01", "2026-12-30")
		self.assertEqual(months(e), [date(2026, 10, 1), date(2026, 11, 1)])

	def test_case_f_continuous_applications(self):
		"""#14・育休F：11/5〜11/10 と 11/11〜11/20 の連続する2件は1回の休業（16日）として11月が免除。"""
		e = self.emp()
		leave(e, sf.LEAVE_CHILDCARE, "2026-11-05", "2026-11-10")
		leave(e, sf.LEAVE_CHILDCARE, "2026-11-11", "2026-11-20")
		self.assertEqual(months(e), [date(2026, 11, 1)])

	def test_early_return_by_amendment(self):
		"""SIR-50：月末1日前に復帰（休暇申請を 11/29 終了に修正）すると11月は月末在休にならない。"""
		e = self.emp()
		app = leave(e, sf.LEAVE_CHILDCARE, "2026-11-20", "2026-12-15")
		self.assertEqual(months(e), [date(2026, 11, 1)])
		app.cancel()
		amended = frappe.copy_doc(app)
		amended.docstatus = 0
		amended.amended_from = app.name
		amended.to_date = "2026-11-29"
		amended.status = "Approved"
		amended.insert(ignore_permissions=True)
		amended.submit()
		self.assertEqual(months(e), [])
		self.assertEqual(exemption.get_exemption_summary(e)["months"], [])

	def test_postnatal_work_days_excluded(self):
		"""出生時育児休業中の就業日は14日の計算から除く（11/1〜11/14 のうち1日就業で13日）。"""
		e = self.emp()
		app = leave(e, sf.LEAVE_POSTNATAL, "2026-11-01", "2026-11-14")
		self.assertEqual(months(e), [date(2026, 11, 1)])
		app.db_set("jp_si_work_days", "2026-11-07")
		self.assertEqual(months(e), [])

	def test_slip_exempt_month(self):
		"""#8・SIR-52：免除月（11月分）は健保・支援金・厚年・拠出金が0円、雇用保険料は免除されない。"""
		e = self.emp()
		leave(e, sf.LEAVE_CHILDCARE, "2026-11-20", "2026-12-15")
		slip = sf.make_slip(e, "2026-12-01")  # 12月支給 → 11月分
		self.assertEqual(sf.deduction(slip, C.K_HEALTH), 0)
		self.assertEqual(sf.deduction(slip, C.K_PENSION), 0)
		self.assertEqual(sf.deduction(slip, C.K_SUPPORT), 0)
		child = sf.lines(slip, C.LINE_MONTHLY, C.P_CHILD)
		self.assertTrue(child and child[0].exempt and child[0].employer_amount == 0)
		self.assertEqual(sf.employer(slip, C.K_ER_CHILD), 0)
		self.assertTrue(sf.lines(slip, C.LINE_EMPLOYMENT))
		self.assertIn("免除", slip.jp_si_warnings)

	def test_bonus_exemption_case_d(self):
		"""#8・育休D・SIR-51：12/1〜12/31（ちょうど1か月）の育休中の12/10 の賞与は免除されない。1か月超なら免除。"""
		e = self.emp()
		leave(e, sf.LEAVE_CHILDCARE, "2026-12-01", "2026-12-31")
		slip = sf.make_slip(e, "2026-12-01", posting_day=10, additional=[(sf.BONUS, 500000)])
		self.assertTrue(sf.lines(slip, C.LINE_BONUS, C.P_PENSION)[0].employee_amount > 0)
		e2 = self.emp()
		leave(e2, sf.LEAVE_CHILDCARE, "2026-12-01", "2027-01-05")
		slip2 = sf.make_slip(e2, "2026-12-01", posting_day=10, additional=[(sf.BONUS, 500000)])
		pension = sf.lines(slip2, C.LINE_BONUS, C.P_PENSION)
		self.assertTrue(pension and pension[0].exempt and pension[0].employee_amount == 0)
		self.assertEqual(slip2.jp_bonus_health_standard, 500000)  # 免除でも累計には算入
