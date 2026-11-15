"""賞与の社会保険料と雇用保険・労災保険（社会保険・雇用保険の計算規則 v1.0 8章 #5・#9・#13、SIR-30〜33・60〜65）。"""

import frappe

from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase
from japan_hr_compliance.tests import si_fixtures as sf
from japan_hr_compliance.tests.test_si_monthly import SITestCase


def bonus_lines(slip, kind):
	return sf.lines(slip, C.LINE_BONUS, kind)


def bonus_slip(employee, pay_date, amount, posting_date=None, **kw):
	"""賞与だけの明細（計算期間は支給日の1日）。"""
	return sf.make_slip(employee, pay_date, posting_date=posting_date or pay_date, start_date=pay_date, end_date=pay_date,
		payroll_frequency="", salary_structure=sf.STRUCTURE, jp_is_bonus=1, additional=[(sf.BONUS, amount)], **kw)


class TestBonusPremium(SITestCase):
	def test_annual_cap_6_5(self):
		"""#5・6.5・SIR-30・31：6/30 に300万円、12/10 に3,200,456円。2回目の健保標準賞与額は 2,730,000円。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		june = sf.make_slip(emp.name, "2026-06-01", posting_day=30, additional=[(sf.BONUS, 3000000)], submit=True)
		self.assertEqual(june.jp_bonus_health_standard, 3000000)
		self.assertEqual(june.jp_bonus_pension_standard, 1500000)
		dec = sf.make_slip(emp.name, "2026-12-01", posting_day=10, additional=[(sf.BONUS, 3200456)])
		self.assertEqual(dec.jp_bonus_standard, 3200000)
		self.assertEqual(dec.jp_bonus_health_standard, 2730000)
		self.assertEqual(dec.jp_bonus_pension_standard, 1500000)
		self.assertEqual(bonus_lines(dec, C.P_HEALTH_CARE)[0].employee_amount, 156565)
		self.assertEqual(bonus_lines(dec, C.P_SUPPORT)[0].employee_amount, 3139)
		self.assertEqual(bonus_lines(dec, C.P_PENSION)[0].employee_amount, 137250)
		# 月額（11月分）と賞与の合計が控除行になる
		self.assertEqual(sf.deduction(dec, C.K_PENSION), 15555 + 137250)
		dec.submit()
		acc = frappe.get_doc("JP Bonus Accumulation", f"{emp.name}-2026")
		self.assertEqual(acc.total_amount, 5730000)
		dec.cancel()
		acc.reload()
		self.assertEqual(acc.total_amount, 3000000)

	def test_prior_employer_cumulative(self):
		"""SIR-31：前職（協会けんぽ）の累計を手入力すると上限の計算に含まれる。"""
		emp = sf.make_employee(joining="2026-05-01", birth="1981-05-10", health=170000)
		frappe.get_doc({"doctype": "JP Bonus Accumulation", "employee": emp.name, "fiscal_year": 2026,
			"prior_employer_amount": 5000000}).insert()
		slip = sf.make_slip(emp.name, "2026-07-01", posting_day=10, additional=[(sf.BONUS, 1000000)])
		self.assertEqual(slip.jp_bonus_health_standard, 730000)

	def test_pension_monthly_cap_same_month(self):
		"""#5・SIR-32：同じ月の2回の賞与で厚年の標準賞与額は合計150万円に抑えられる。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		sf.make_slip(emp.name, "2026-07-01", posting_day=10, additional=[(sf.BONUS, 1000000)], submit=True)
		second = bonus_slip(emp.name, "2026-07-31", 800000)
		self.assertEqual(second.jp_bonus_pension_standard, 500000)
		self.assertEqual(second.jp_bonus_health_standard, 800000)
		self.assertEqual(second.jp_si_target_months, "")  # 賞与明細では月額を計算しない

	def test_loss_month_bonus(self):
		"""#5・SIR-33：12/30 退職（喪失 12/31）で12/10 に支給した賞与は保険料0円、累計には算入し届出の対象。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000, relieving="2026-12-30")
		slip = sf.make_slip(emp.name, "2026-12-01", posting_day=10, end_date="2026-12-30", additional=[(sf.BONUS, 500000)])
		self.assertFalse(bonus_lines(slip, C.P_HEALTH_CARE))
		self.assertFalse(bonus_lines(slip, C.P_PENSION))
		self.assertEqual(slip.jp_bonus_health_standard, 500000)
		self.assertTrue(slip.jp_bonus_report_required)
		self.assertIn("喪失月", slip.jp_bonus_health_treatment)
		slip.submit()
		self.assertEqual(frappe.db.get_value("JP Bonus Accumulation", f"{emp.name}-2026", "total_amount"), 500000)

	def test_same_month_gain_loss_bonus(self):
		"""#13・SIR-33：同月得喪の月（11/10 入社・11/25 退職）に支給した賞与は保険料がかかる。"""
		emp = sf.make_employee(joining="2026-11-10", birth="1981-05-10", health=170000, relieving="2026-11-25")
		slip = sf.make_slip(emp.name, "2026-11-01", posting_day=20, end_date="2026-11-25", additional=[(sf.BONUS, 300000)])
		self.assertTrue(bonus_lines(slip, C.P_PENSION))
		self.assertEqual(bonus_lines(slip, C.P_PENSION)[0].employee_amount, 27450)

	def test_bonus_after_loss_not_covered(self):
		"""#13・SIR-33：資格喪失後に支給した賞与は保険料・届出とも対象外。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000, relieving="2026-11-25")
		slip = bonus_slip(emp.name, "2026-11-25", 300000, posting_date="2026-12-05")
		self.assertFalse(bonus_lines(slip, C.P_HEALTH_CARE))
		self.assertEqual(slip.jp_bonus_health_standard, 0)
		self.assertFalse(slip.jp_bonus_report_required)

	def test_four_times_component_is_remuneration(self):
		"""SIR-33：年4回と定めた支給項目は賞与として計算されない。"""
		comp = sf.ensure_component("テスト季節手当", "TSEAS", jp_si_pay_class=C.PAY_CLASS_BONUS, jp_annual_payments=4)
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		slip = sf.make_slip(emp.name, "2026-07-01", additional=[(comp.name, 200000)])
		self.assertFalse(sf.lines(slip, C.LINE_BONUS))
		self.assertEqual(slip.jp_bonus_standard, 0)

	def test_frequency_warning(self):
		"""SIR-33：回数が規程で明らかでない項目が7月1日以前1年間に4回以上支給されていると警告する。"""
		comp = sf.ensure_component("テスト臨時手当", "TTEMP", jp_si_pay_class=C.PAY_CLASS_BONUS, jp_annual_payments=0)
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		for month in ("2025-08-01", "2025-11-01", "2026-02-01", "2026-05-01"):
			sf.make_slip(emp.name, month, additional=[(comp.name, 50000)], submit=True)
		slip = sf.make_slip(emp.name, "2026-08-01", additional=[(comp.name, 50000)])
		self.assertIn("4回以上", slip.jp_si_warnings)


class TestEmploymentInsurance(SITestCase):
	def test_rate_switch_by_closing_date(self):
		"""#9・SIR-60：3月締め4月払いは2025年度（5.5/1000）、4月締め4月払いは2026年度（5/1000）。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", base=300000, health=300000)
		march = sf.make_slip(emp.name, "2026-03-01", posting_date="2026-04-25")
		april = sf.make_slip(emp.name, "2026-04-01", posting_day=25)
		self.assertEqual(sf.deduction(march, C.K_EMPLOYMENT), 1650)
		self.assertEqual(sf.deduction(april, C.K_EMPLOYMENT), 1500)

	def test_rate_switch_by_payment_date(self):
		"""SIR-60：切替基準を支給日にすると、3月締め4月払いも2026年度の料率になる。"""
		sf.set_settings(ei_rate_basis=C.BASIS_PAYMENT)
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", base=300000, health=300000)
		march = sf.make_slip(emp.name, "2026-03-01", posting_date="2026-04-25")
		self.assertEqual(sf.deduction(march, C.K_EMPLOYMENT), 1500)

	def test_target_wage_and_rounding_6_8(self):
		"""6.8・SIR-62：対象賃金 263,500円（通勤手当を含む）× 5/1000 ＝ 1,317.50 → 1,317円。退職金は対象外。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", base=250000, health=260000)
		slip = sf.make_slip(emp.name, "2026-06-01", additional=[(sf.COMMUTE, 13500), (sf.SEVERANCE, 1000000)])
		line = sf.lines(slip, C.LINE_EMPLOYMENT)[0]
		self.assertEqual(line.base_amount, 263500)
		self.assertEqual(line.employee_amount, 1317)
		self.assertAlmostEqual(line.employer_amount, 263500 * 8.5 / 1000)
		self.assertAlmostEqual(sf.employer(slip, C.K_ER_EMPLOYMENT), round(263500 * 8.5 / 1000))

	def test_officer_excluded(self):
		"""#9・SIR-61：役員は雇用保険・労災の対象外。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", jp_si_exempt_reason="役員（雇用保険の対象外）")
		slip = sf.make_slip(emp.name, "2026-06-01")
		self.assertFalse(sf.lines(slip, C.LINE_EMPLOYMENT))
		self.assertFalse(sf.lines(slip, C.LINE_WORKERS_COMP))

	def test_over_65_charged(self):
		"""#9・SIR-61：65歳以上の被保険者にも雇用保険料が計算される。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1958-05-10", base=300000, health=300000)
		slip = sf.make_slip(emp.name, "2026-06-01")
		self.assertEqual(sf.deduction(slip, C.K_EMPLOYMENT), 1500)

	def test_not_insured(self):
		"""SIR-61：雇用保険の被保険者でない者は計算しない（労災は全労働者が対象）。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", ei=False)
		slip = sf.make_slip(emp.name, "2026-06-01")
		self.assertFalse(sf.lines(slip, C.LINE_EMPLOYMENT))
		self.assertTrue(sf.lines(slip, C.LINE_WORKERS_COMP))

	def test_workers_comp_employer(self):
		"""SIR-64：労災保険料（事業主のみ）＝ 対象賃金 × 業種の料率（例示 3/1000）。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", base=300000, health=300000)
		slip = sf.make_slip(emp.name, "2026-06-01")
		line = sf.lines(slip, C.LINE_WORKERS_COMP)[0]
		self.assertEqual(line.employee_amount, 0)
		self.assertAlmostEqual(line.employer_amount, 900)
		self.assertEqual(line.rate_version, sf.WC_CODE)
		self.assertAlmostEqual(sf.employer(slip, C.K_ER_WORKERS_COMP), 900)
		self.assertEqual(frappe.db.get_value("JP Workplace", fx.WORKPLACE_HQ, "wc_business_type"), sf.WC_CODE)


class TestOfficerTypeFromExemptReason(JPHRTestCase):
	"""役員区分は B-1 の jp_si_exempt_reason から導く（役員区分の統合）。"""

	def test_mapping(self):
		from japan_hr_compliance.jp_social_insurance import employee_data as ed

		self.assertEqual(ed.officer_type_from_reason("役員（雇用保険の対象外）"), C.OFFICER)
		self.assertEqual(ed.officer_type_from_reason("兼務役員（雇保は労働者部分で判定）"), C.CONCURRENT_OFFICER)
		self.assertIsNone(ed.officer_type_from_reason("適用除外（雇用保険）"))
		self.assertIsNone(ed.officer_type_from_reason(None))

	def test_no_officer_custom_field(self):
		self.assertFalse(frappe.db.exists("Custom Field", "Employee-jp_officer_type"))
