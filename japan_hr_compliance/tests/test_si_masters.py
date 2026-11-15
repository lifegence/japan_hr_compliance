"""マスタの版管理・CSV 取込みと突合・適用判定・人数の警告・差異レポート（社会保険・雇用保険の計算規則 v1.0
8章 #1・#10・#11、SIR-01〜06・27・70〜73）。"""

from datetime import date
from decimal import Decimal

import frappe

from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import eligibility as el
from japan_hr_compliance.jp_social_insurance import masters
from japan_hr_compliance.jp_social_insurance.calc.tests import fixtures as cf
from japan_hr_compliance.jp_social_insurance.report.jp_si_notice_reconciliation import (
	jp_si_notice_reconciliation as notice,
)
from japan_hr_compliance.jp_social_insurance.report.jp_si_premium_register import (
	jp_si_premium_register as register,
)
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests import si_fixtures as sf
from japan_hr_compliance.tests.test_si_monthly import SITestCase


class TestVersionGovernance(SITestCase):
	def test_confirmation_requires_source(self):
		"""SIR-06・5.3：確認済にするには出典（資料名・公表日・URL）と確認者・確認日が必要。"""
		doc = frappe.get_doc({"doctype": "JP Pension Rate", "effective_from": "2030-01-01", "pension_rate": 18.3,
			"child_contribution_rate": 0.4, "status": C.STATUS_CONFIRMED})
		with self.assertRaises(frappe.ValidationError):
			doc.insert()
		doc.update(sf.SOURCE)
		doc.insert()
		self.assertEqual(str(doc.effective_from), "2030-01-01")

	def test_effective_month_normalized_and_duplicate(self):
		"""5.2：適用開始は月の1日に直す。同じ適用開始の確認済の版は2件持てない。"""
		doc = sf.ensure_version("JP Bonus Cap", {"effective_from": "2031-04-15"}, {"health_annual_cap": 5730000,
			"pension_monthly_cap": 1500000})
		self.assertEqual(str(doc.effective_from), "2031-04-01")
		with self.assertRaises(frappe.ValidationError):
			sf._confirmed(frappe.get_doc({"doctype": "JP Bonus Cap", "effective_from": "2031-04-01",
				"health_annual_cap": 1, "pension_monthly_cap": 1})).insert()

	def test_insurer_kyokai_only(self):
		"""SIR-02：保険者は協会けんぽのみ。"""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "JP Health Insurance Rate", "effective_from": "2030-03-01", "insurer": "健保組合",
				"rates": [{"prefecture": sf.TOKYO, "health_rate": 9}]}).insert()

	def test_used_version_locked_and_correction(self):
		"""SIR-06・5.3：給与明細で使われた版は値を変更・削除できない。訂正は訂正版を登録し、元の版を廃止にする。"""
		version = sf.ensure_version("JP Pension Rate", {"effective_from": "2027-01-01"},
			{"pension_rate": 18.3, "child_contribution_rate": 0.36})
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		slip = sf.make_slip(emp.name, "2027-02-01", submit=True)  # 2027年1月分
		pension_line = sf.lines(slip, C.LINE_MONTHLY, C.P_PENSION)[0]
		self.assertEqual(pension_line.rate_version, version.name)
		self.assertTrue(masters.is_version_used("JP Pension Rate", version.name))
		self.assertEqual([s.name for s in masters.get_affected_salary_slips("JP Pension Rate", version.name)], [slip.name])
		version.reload()
		version.pension_rate = 18.4
		with self.assertRaises(frappe.ValidationError):
			version.save()
		version.reload()
		with self.assertRaises(frappe.ValidationError):
			version.delete()
		# 訂正版：元の版を廃止（訂正済）にしてから新しい版を確認済にする
		version.reload()
		version.status = C.STATUS_SUPERSEDED
		version.superseded_by = "（訂正版）"
		version.save()
		corrected = sf.ensure_version("JP Pension Rate", {"effective_from": "2027-01-01", "pension_rate": 18.4},
			{"child_contribution_rate": 0.36})
		self.assertEqual(corrected.status, C.STATUS_CONFIRMED)
		slip2 = sf.make_slip(sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000).name, "2027-02-01")
		self.assertEqual(sf.lines(slip2, C.LINE_MONTHLY, C.P_PENSION)[0].rate_version, corrected.name)

	def test_grade_table_locked_after_history(self):
		"""SIR-06：確定済みの標準報酬月額の履歴の基礎になった等級表は変更できない。"""
		name = frappe.db.get_value("JP Standard Remuneration Table", {"kind": C.TABLE_HEALTH, "effective_from": "2020-09-01"})
		sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		self.assertTrue(masters.is_version_used("JP Standard Remuneration Table", name))
		doc = frappe.get_doc("JP Standard Remuneration Table", name)
		doc.grades[0].standard = 57000
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_workers_comp_used_row_locked(self):
		"""SIR-05・06：給与明細で使われた労災保険率の行は変更できない。訂正行は追加できる。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10")
		sf.make_slip(emp.name, "2026-06-01", submit=True)
		doc = frappe.get_doc("JP Workers Comp Rate", sf.WC_CODE)
		doc.rates[0].rate_permille = 4
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		doc.reload()
		doc.append("rates", {"effective_from": "2027-04-01", "rate_permille": 2.5, "status": C.STATUS_DRAFT})
		doc.save()
		self.assertEqual(len(doc.rates), 2)


class TestCsvAndReconciliation(SITestCase):
	def test_import_grades_csv(self):
		"""5.3：等級表を CSV で取り込める。"""
		doc = frappe.get_doc({"doctype": "JP Standard Remuneration Table", "kind": C.TABLE_PENSION,
			"effective_from": "2027-09-01"}).insert()
		rows = ["grade,standard,lower,upper"] + [
			f"{i + 1},{std},{lower},{cf._PENSION_ROWS[i + 1][1] if i + 1 < len(cf._PENSION_ROWS) else ''}"
			for i, (std, lower) in enumerate(cf._PENSION_ROWS)]
		rows[-1] = "32,680000,665000,"  # 2027年9月の上限引上げ（68万円）の例
		rows[-2] = rows[-2].rsplit(",", 1)[0] + ",665000"
		self.assertEqual(doc.import_grades_csv(content="\n".join(rows)), 32)
		doc.reload()
		self.assertEqual(doc.grades[-1].standard, 680000)

	def test_reconcile_premium_table_1(self):
		"""#1・5.3：保険料額表の折半額と全等級を突合し、結果を版に添付する（不一致の等級を示す）。"""
		version = frappe.get_doc("JP Health Insurance Rate", {"effective_from": "2026-04-01", "status": C.STATUS_CONFIRMED})
		lines = ["prefecture,grade,health_half,health_care_half,support_half"]
		for i, (std, _lower) in enumerate(cf._HEALTH_ROWS):
			std = Decimal(std)
			h = std * Decimal("0.0985") / 2
			hc = std * Decimal("0.1147") / 2
			sp = std * Decimal("0.0023") / 2
			if i == 9:
				hc += 1  # 額表の誤りを模した1件
			lines.append(f"{sf.TOKYO},{i + 1},{h},{hc},{sp}")
		mismatches = version.reconcile_premium_table(content="\n".join(lines))
		self.assertEqual(mismatches, 1)
		version.reload()
		results = {r.item: r for r in version.reconciliations}
		self.assertEqual(results["健保（介護非該当）"].mismatches, 0)
		self.assertEqual(results["健保＋介護"].mismatches, 1)
		self.assertEqual(results["健保＋介護"].grades_checked, 50)
		self.assertEqual(results["子ども・子育て支援金"].mismatches, 0)


class TestEligibility(SITestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("JP Standard Worker Condition", {"company": fx.COMPANY, "workplace": ("is", "not set")}):
			frappe.get_doc({"doctype": "JP Standard Worker Condition", "company": fx.COMPANY, "valid_from": "2024-01-01",
				"weekly_hours": 40, "monthly_days": 21}).insert(ignore_permissions=True)

	def part_timer(self, hours_h, hours_m=0, monthly_days=16, base=80000, **contract):
		values = {"employment_class": "パートタイマー", "work_rule_class": "パートタイマー用", "weekly_hours_h": hours_h,
			"weekly_hours_m": hours_m, "monthly_days": monthly_days, "weekly_days": 4}
		values.update(contract)
		return sf.make_employee(joining="2025-04-01", birth="1990-05-10", base=base, insured=False, ei=False,
			contract=values).name

	def test_three_quarters_boundary(self):
		"""#10・SIR-70：週30時間・月16日（通常40時間・21日の4分の3以上）は一般、29時間30分は一般にならない。"""
		self.assertEqual(el.classify_employee(self.part_timer(30), "2026-11-01").social, "一般")
		self.assertEqual(el.classify_employee(self.part_timer(29, 30), "2026-11-01").social, "適用なし")

	def test_wage_requirement_by_effective_date(self):
		"""#10・SIR-71：特定適用事業所で週25時間・月額8万円の者は、賃金要件の撤廃（2026-10-01）の前は適用なし、後は短時間労働者。"""
		sf.set_settings(specific_applicable_office=1, specific_applicable_from="2024-10-01")
		e = self.part_timer(25)
		self.assertEqual(el.classify_employee(e, "2026-09-30").social, "適用なし")
		self.assertEqual(el.classify_employee(e, "2026-10-01").social, "短時間労働者")
		# 施行日を後から修正できる
		rule = frappe.get_doc("JP SI Eligibility Rule", {"effective_from": "2026-10-01"})
		rule.effective_from = "2026-11-01"
		rule.save()
		self.assertEqual(el.classify_employee(e, "2026-10-15").social, "適用なし")

	def test_not_specific_office(self):
		"""SIR-71：特定適用事業所でなければ4分の3未満の者は適用なし（自動判定しない）。"""
		self.assertEqual(el.classify_employee(self.part_timer(25), "2026-11-01").social, "適用なし")

	def test_twenty_hours_boundary(self):
		"""#10・SIR-73：週20時間の境界で社保（短時間労働者）・雇保の区分が変わる。昼間学生は対象外。"""
		sf.set_settings(specific_applicable_office=1)
		e20, e19 = self.part_timer(20), self.part_timer(19, 30)
		self.assertEqual(el.classify_employee(e20, "2026-11-01").social, "短時間労働者")
		self.assertTrue(el.classify_employee(e20, "2026-11-01").employment)
		self.assertEqual(el.classify_employee(e19, "2026-11-01").social, "適用なし")
		self.assertFalse(el.classify_employee(e19, "2026-11-01").employment)
		student = self.part_timer(20, student_type="昼間学生")
		self.assertEqual(el.classify_employee(student, "2026-11-01").social, "適用なし")
		self.assertFalse(el.classify_employee(student, "2026-11-01").employment)

	def test_review_lists_differences(self):
		"""SIR-72：登録上の資格と判定が異なる者が一覧に出る（自動変更しない）。"""
		sf.set_settings(specific_applicable_office=1)
		e = self.part_timer(25)
		rows = {r.employee: r for r in el.review(fx.COMPANY, "2026-11-01")}
		self.assertTrue(rows[e].differs)
		self.assertEqual(rows[e].current_social, "適用なし")
		self.assertFalse(frappe.db.exists("JP Employee Insurance Status", {"employee": e}))

	def test_headcount_warning(self):
		"""SIR-71：厚年被保険者数が企業規模要件（51人）以上の月が12か月中6か月以上になると警告する。"""
		company = fx.COMPANY
		for i, count in enumerate([40, 52, 53, 55, 51, 60]):
			doc = el.record_headcount(company, date(2026, 1 + i, 1), count=count)
		self.assertEqual(doc.threshold, 51)
		self.assertFalse(doc.warning)
		doc = el.record_headcount(company, date(2026, 7, 1), count=58)
		self.assertTrue(doc.warning)
		self.assertTrue(doc.at_or_above)

	def test_count_pension_insured(self):
		"""SIR-71：厚年被保険者数は短時間労働者を除いて数える。"""
		before = el.count_pension_insured(fx.COMPANY, "2026-11-01")
		sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000)
		short = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000, insured=False)
		sf.add_status(short.name, C.INS_PENSION, "2025-04-01", category=C.CATEGORY_SHORT_TIME)
		self.assertEqual(el.count_pension_insured(fx.COMPANY, "2026-11-01"), before + 1)

	def test_status_syncs_b1_fields(self):
		"""SIR-11：資格履歴の最新の期間が Employee の資格取得日・喪失日（B-1）に反映される。重複はエラー。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=170000, relieving="2026-12-31")
		values = frappe.db.get_value("Employee", emp.name, ["jp_hi_acquired", "jp_hi_lost"], as_dict=True)
		self.assertEqual((str(values.jp_hi_acquired), str(values.jp_hi_lost)), ("2025-04-01", "2027-01-01"))
		with self.assertRaises(frappe.ValidationError):
			sf.add_status(emp.name, C.INS_HEALTH, "2026-06-01")


class TestReports(SITestCase):
	def test_notice_reconciliation_11(self):
		"""#11・SIR-27：試算した納入告知額と個人別の事業主負担の合計の差が端数（0以上1円未満）の範囲に収まる。"""
		names = []
		for health in (142000, 170000, 98000):
			emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=health)
			names.append(sf.make_slip(emp.name, "2026-06-01", submit=True).name)
		columns, rows = notice.execute({"company": fx.COMPANY, "month": "2026-05-01", "notice_pension": 74000})
		by = {r["premium"]: r for r in rows}
		health = by["健康保険（介護含む）"]
		self.assertGreaterEqual(health["people"], 3)
		self.assertTrue(0 <= health["difference"] < 1)
		self.assertEqual(health["notice_amount"], int(health["total"]))
		self.assertTrue(0 <= by["厚生年金"]["difference"] < 1)
		self.assertIsNotNone(by["厚生年金"]["notice_difference"])

	def test_register_detects_employee_exceeding_employer(self):
		"""2.2・9.1 #3：標準報酬月額 142,000円×11.47% で本人 8,144円＞事業主 8,143.4円を検出する（金額は調整しない）。"""
		emp = sf.make_employee(joining="2025-04-01", birth="1981-05-10", health=142000)
		slip = sf.make_slip(emp.name, "2026-06-01", submit=True)
		self.assertEqual(sf.deduction(slip, C.K_HEALTH_CARE), 8144)
		self.assertIn("上回って", slip.jp_si_warnings)
		_cols, rows = register.execute({"company": fx.COMPANY, "from_date": "2026-06-01", "to_date": "2026-06-30",
			"employee": emp.name, "only_exceeds": 1})
		self.assertEqual([r.premium_kind for r in rows], [C.P_HEALTH_CARE])
		self.assertAlmostEqual(rows[0].employer_amount, 8143.4)
