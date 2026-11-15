"""標準報酬月額の決定：定時決定・随時改定・保険者算定・休業終了時改定（社会保険・雇用保険の計算規則 v1.0
8章 #6・#7、6.7、SIR-12・40〜48）。"""

from datetime import date

import frappe

from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import determination as det
from japan_hr_compliance.jp_social_insurance.doctype.jp_standard_remuneration_history.jp_standard_remuneration_history import (
	propose_acquisition_standard,
)
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests import si_fixtures as sf
from japan_hr_compliance.tests.test_si_monthly import SITestCase

DOCTYPE = "JP Remuneration Determination"


def manual(employee, determination_type, months, category=C.DAYS_GENERAL, insert=True, **values):
	"""months：[(支払月, 支払基礎日数, 通貨, 遡及差額, 除外理由), ...]"""
	doc = frappe.get_doc({"doctype": DOCTYPE, "employee": employee, "determination_type": determination_type,
		"days_category": category, **values,
		"months": [{"pay_month": m, "base_days": d, "cash": cash, "retroactive_pay": retro, "excluded_reason": reason}
			for m, d, cash, retro, reason in months]})
	if insert:
		doc.insert(ignore_permissions=True)
	return doc


def q(cash_by_days, year=2026):
	return [(date(year, 4 + i, 1), days, cash, 0, None) for i, (days, cash) in enumerate(cash_by_days)]


class TestRegularDetermination(SITestCase):
	def test_case_a_from_slips(self):
		"""#6・SIR-40・46：4〜6月の給与実績を手入力なしで集計し、確定で9月分から適用される。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", base=300000, health=260000)
		for m in ("2026-04-01", "2026-05-01", "2026-06-01"):
			sf.make_slip(emp.name, m, submit=True)
		created = det.create_regular_determinations(fx.COMPANY, 2026)
		name = frappe.db.get_value(DOCTYPE, {"employee": emp.name, "year": 2026})
		self.assertIn(name, created)
		doc = frappe.get_doc(DOCTYPE, name)
		self.assertEqual([r.base_days for r in doc.months], [30, 31, 30])
		self.assertEqual([r.included for r in doc.months], [1, 1, 1])
		self.assertEqual(doc.adjusted_average, 300000)
		self.assertEqual(doc.new_health_standard, 300000)
		self.assertEqual(doc.new_pension_standard, 300000)
		self.assertEqual(str(doc.effective_from), "2026-09-01")
		# 確定前は保険料計算に使われない（SIR-46）
		before = sf.make_slip(emp.name, "2026-10-01")
		self.assertEqual(sf.lines(before, C.LINE_MONTHLY, C.P_PENSION)[0].base_amount, 260000)
		before.delete()
		doc.submit()
		hist = frappe.get_doc("JP Standard Remuneration History", doc.history)
		self.assertEqual((hist.decision_type, hist.docstatus, hist.health_grade), (C.DECISION_REGULAR, 1, 22))
		after = sf.make_slip(emp.name, "2026-10-01")  # 9月分
		self.assertEqual(sf.lines(after, C.LINE_MONTHLY, C.P_PENSION)[0].base_amount, 300000)
		doc.cancel()
		self.assertEqual(frappe.db.get_value("JP Standard Remuneration History", doc.history, "docstatus"), 2)

	def test_cases_b_to_e(self):
		"""#6・6.7・SIR-42：支払基礎日数の規則で平均の対象月が決まる。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=200000)
		cases = [
			(C.DAYS_PART_TIME, [(18, 200000), (16, 100000), (15, 100000)], [1, 0, 0]),  # B：4月のみ
			(C.DAYS_PART_TIME, [(16, 200000), (16, 200000), (15, 200000)], [1, 1, 1]),  # C：3か月とも
			(C.DAYS_PART_TIME, [(14, 100000), (16, 200000), (12, 100000)], [0, 1, 0]),  # D：5月のみ
			(C.DAYS_SHORT_TIME, [(12, 120000), (10, 90000), (11, 110000)], [1, 0, 1]),  # E：4月・6月
		]
		for category, months, included in cases:
			doc = manual(emp.name, C.DET_REGULAR, q(months), category=category, year=2026, insert=False)
			doc.run_calculation()
			self.assertEqual([r.included for r in doc.months], included, category)
		# ケースE：平均 115,000円 → 118,000円の等級（健保・厚年とも）
		self.assertEqual(doc.adjusted_average, 115000)
		self.assertEqual((doc.new_health_standard, doc.new_pension_standard), (118000, 118000))

	def test_previous_when_no_month_qualifies(self):
		"""6.7：3か月とも基準に満たない場合は従前の標準報酬月額で決定する。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=200000)
		doc = manual(emp.name, C.DET_REGULAR, q([(10, 300000), (12, 300000), (9, 300000)]), year=2026)
		self.assertEqual(doc.new_health_standard, 200000)
		self.assertIn("従前", doc.result_note)

	def test_mid_join_and_retroactive(self):
		"""#6・SIR-43：途中入社の月は除き、遡及差額を含む月は差額を除いて平均する。"""
		emp = sf.make_employee(joining="2026-04-10", birth="1981-05-10", base=300000, health=300000)
		for m in ("2026-04-01", "2026-05-01"):
			sf.make_slip(emp.name, m, submit=True)
		sf.make_slip(emp.name, "2026-06-01", submit=True, additional=[(sf.RETRO, 30000)])
		doc = det.new_determination(emp.name, C.DET_REGULAR, year=2026)
		self.assertTrue(doc.months[0].excluded_reason)
		self.assertEqual([r.included for r in doc.months], [0, 1, 1])
		self.assertEqual(doc.months[2].retroactive_pay, 30000)
		self.assertEqual(doc.average, 315000)
		self.assertEqual(doc.adjusted_average, 300000)

	def test_not_target_after_june_acquisition(self):
		"""SIR-43：6月1日以降の資格取得者は定時決定の対象にしない。"""
		emp = sf.make_employee(joining="2026-06-01", birth="1981-05-10", health=200000)
		created = det.create_regular_determinations(fx.COMPANY, 2026)
		self.assertFalse(frappe.db.exists(DOCTYPE, {"employee": emp.name}))
		self.assertIsInstance(created, list)


class TestOccasionalRevision(SITestCase):
	def test_fixed_wage_increase_candidate(self):
		"""#7・SIR-44〜46：固定的賃金が増えた者が候補になり、2等級以上の差で4か月目から改定される。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", base=300000, health=300000)
		for m in ("2026-01-01", "2026-02-01", "2026-03-01"):
			sf.make_slip(emp.name, m, submit=True)
		ssa = frappe.get_doc({"doctype": "Salary Structure Assignment", "employee": emp.name,
			"salary_structure": sf.STRUCTURE, "company": fx.COMPANY, "from_date": "2026-04-01", "base": 400000,
			"currency": "JPY", "payroll_payable_account": frappe.db.get_value("Company", fx.COMPANY,
				"default_payroll_payable_account")})
		ssa.insert(ignore_permissions=True)
		ssa.submit()
		for m in ("2026-04-01", "2026-05-01", "2026-06-01"):
			sf.make_slip(emp.name, m, submit=True)
		created = det.extract_occasional_candidates(fx.COMPANY, "2026-06-01")
		name = frappe.db.get_value(DOCTYPE, {"employee": emp.name, "determination_type": C.DET_OCCASIONAL})
		self.assertIn(name, created)
		doc = frappe.get_doc(DOCTYPE, name)
		self.assertEqual((str(doc.change_month), doc.direction), ("2026-04-01", C.DIRECTION_INCREASE))
		self.assertTrue(doc.applicable)
		self.assertEqual(doc.new_health_standard, 410000)
		self.assertEqual(str(doc.effective_from), "2026-07-01")
		doc.submit()
		self.assertEqual(frappe.db.get_value("JP Standard Remuneration History", doc.history, "decision_type"),
			C.DECISION_OCCASIONAL)
		# 2回目の抽出では同じ候補を作らない
		self.assertNotIn(name, det.extract_occasional_candidates(fx.COMPANY, "2026-06-01"))

	def test_non_fixed_only_not_candidate(self):
		"""#7・SIR-44：非固定的賃金（残業手当）だけの増減は候補にならない。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", base=300000, health=300000)
		for m, overtime in (("2026-03-01", 0), ("2026-04-01", 100000), ("2026-05-01", 100000), ("2026-06-01", 100000)):
			sf.make_slip(emp.name, m, submit=True, additional=[(sf.OVERTIME, overtime)] if overtime else None)
		det.extract_occasional_candidates(fx.COMPANY, "2026-06-01")
		self.assertFalse(frappe.db.exists(DOCTYPE, {"employee": emp.name, "determination_type": C.DET_OCCASIONAL}))

	def test_upper_limit_special_case(self):
		"""#7・SIR-45：厚年の上限の1等級差の特例（境界値 665,000円は等級表から導出）。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=620000)
		months = [(date(2026, 4 + i, 1), 30, 665000, 0, None) for i in range(3)]
		doc = manual(emp.name, C.DET_OCCASIONAL, months, change_month="2026-04-01", direction=C.DIRECTION_INCREASE)
		self.assertTrue(doc.applicable)
		self.assertEqual((doc.new_pension_standard, doc.pension_grade_diff), (650000, 1))
		self.assertIn("特例", doc.result_note)
		months = [(date(2026, 4 + i, 1), 30, 660000, 0, None) for i in range(3)]
		doc2 = manual(emp.name, C.DET_OCCASIONAL, months, change_month="2026-04-01", direction=C.DIRECTION_INCREASE)
		self.assertFalse(doc2.applicable)

	def test_base_days_short(self):
		"""SIR-45：3か月のいずれかの支払基礎日数が17日未満なら改定しない。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=300000)
		months = [(date(2026, 4, 1), 30, 500000, 0, None), (date(2026, 5, 1), 16, 500000, 0, None),
			(date(2026, 6, 1), 30, 500000, 0, None)]
		doc = manual(emp.name, C.DET_OCCASIONAL, months, change_month="2026-04-01", direction=C.DIRECTION_INCREASE)
		self.assertFalse(doc.applicable)
		self.assertEqual(doc.new_health_standard, 300000)


class TestOtherDeterminations(SITestCase):
	def test_manual_insurer_assessment_used(self):
		"""SIR-47：決定区分「保険者算定」で登録した標準報酬月額が保険料計算に使われる。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=300000)
		sf.add_history(emp.name, "2026-09-01", 240000, 240000, decision=C.DECISION_INSURER)
		slip = sf.make_slip(emp.name, "2026-10-01")
		self.assertEqual(sf.lines(slip, C.LINE_MONTHLY, C.P_PENSION)[0].base_amount, 240000)

	def test_draft_history_not_used(self):
		"""SIR-46：確定前（下書き）の標準報酬月額は使われない。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=300000)
		sf.add_history(emp.name, "2026-09-01", 240000, 240000, decision=C.DECISION_INSURER, submit=False)
		slip = sf.make_slip(emp.name, "2026-10-01")
		self.assertEqual(sf.lines(slip, C.LINE_MONTHLY, C.P_PENSION)[0].base_amount, 300000)

	def test_history_grade_display_and_validation(self):
		"""SIR-12：健保・厚年の等級が別々に表示される。等級表にない金額はエラー。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=58000, pension=88000)
		hist = frappe.get_doc("JP Standard Remuneration History", {"employee": emp.name})
		self.assertEqual((hist.health_grade, hist.pension_grade), (1, 1))
		with self.assertRaises(frappe.ValidationError):
			sf.add_history(emp.name, "2026-09-01", 123456, 123456)
		proposal = propose_acquisition_standard(emp.name, "2026-04-01", 300000)
		self.assertEqual((proposal["health_standard"], proposal["pension_standard"]), (300000, 300000))

	def test_annual_average_assessment(self):
		"""SIR-48：年間平均と定時決定の差が2等級以上で季節的変動がある者が候補になり、確定で「保険者算定」として入る。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=300000)
		reg = manual(emp.name, C.DET_REGULAR, q([(30, 300000), (31, 300000), (30, 300000)]), year=2026)
		reg.submit()
		months = [(date(2025, 7 + i, 1) if i < 6 else date(2026, i - 5, 1), 30, 300000 if i >= 9 else 200000, 0, None)
			for i in range(12)]
		doc = manual(emp.name, C.DET_ANNUAL, months, year=2026, regular_determination=reg.name)
		self.assertFalse(doc.applicable)  # 季節的変動の確認前
		self.assertEqual(doc.health_grade_diff, -4)
		doc.seasonal_reason = 1
		doc.filing_required = 1
		doc.save()
		self.assertTrue(doc.applicable)
		with self.assertRaises(frappe.ValidationError):
			doc.submit()  # 本人の同意がない
		doc.reload()
		doc.employee_consent = 1
		doc.save()
		doc.submit()
		hist = frappe.get_doc("JP Standard Remuneration History", doc.history)
		self.assertEqual((hist.decision_type, hist.health_standard, str(hist.effective_from)),
			(C.DECISION_INSURER, 220000, "2026-09-01"))

	def test_leave_end_revision(self):
		"""SIR-48：休業終了日の翌日が属する月以後3か月の報酬から算出し、4か月目から改定する。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1991-05-10", health=300000, gender="Female")
		months = [(date(2026, 7 + i, 1), 20, 240000, 0, None) for i in range(3)]
		doc = manual(emp.name, C.DET_CHILDCARE_END, months, leave_end="2026-06-30")
		self.assertTrue(doc.applicable)
		self.assertEqual((doc.new_health_standard, str(doc.effective_from)), (240000, "2026-10-01"))
		doc.submit()
		self.assertEqual(frappe.db.get_value("JP Standard Remuneration History", doc.history, "decision_type"),
			C.DECISION_CHILDCARE_END)
