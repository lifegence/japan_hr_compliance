"""付与予定の作成・自動確定・出勤率・区分のテスト（B-3 7章 観点 #1〜#11・#25・#26、LVR-01〜08・10〜16・20・24・25）。"""

import json

import frappe
from frappe.utils import add_days, getdate

from japan_hr_compliance.jp_leave import grant_service, jobs
from japan_hr_compliance.jp_leave.utils import CALC, GRANT
from japan_hr_compliance.tests import leave_fixtures as lf
from japan_hr_compliance.tests.test_leave_hrms import LeaveTestCase

EMPLOYER = "使用者側に起因する経営・管理上の障害による休業"
PART = dict(employment_class="パートタイマー", work_rule_class="パートタイマー用", wage_type="時給", daily_hours_h=8, monthly_days=13)


def plan_of(emp, base_date):
	name = frappe.db.get_value(GRANT, {"employee": emp, "grant_date": base_date, "docstatus": ("<", 2)}, "name")
	return frappe.get_doc(GRANT, name) if name else None


class TestLeaveGrant(LeaveTestCase):
	def test_auto_plan_and_confirm(self):
		"""#1・LVR-20 基準日の7日前に付与予定（基準日・勤続・区分・日数・出勤率）が作られ、基準日に確定して hrms の残高ができる。"""
		emp = lf.make_employee("2025-04-01").name
		lf.present_except(emp, "2025-04-01", "2025-09-30")
		lf.run_daily("2025-09-23", emp)
		self.assertIsNone(plan_of(emp, "2025-10-01"), "8日前には作らない")
		lf.run_daily("2025-09-24", emp)
		plan = plan_of(emp, "2025-10-01")
		self.assertEqual((plan.status, plan.docstatus, plan.days, plan.service_months, plan.category), ("予定", 0, 10, 6, "通常"))
		calc = frappe.get_doc(CALC, plan.attendance_calc)
		self.assertEqual((getdate(calc.period_start), getdate(calc.period_end)), (getdate("2025-04-01"), getdate("2025-09-30")))
		self.assertEqual(calc.rate, 100)
		lf.run_daily("2025-10-01", emp)
		plan.reload()
		self.assertEqual((plan.status, plan.docstatus), ("確定", 1))
		self.assertTrue(frappe.db.get_value(CALC, plan.attendance_calc, "frozen"))
		self.assertBalance(emp, "2025-10-01", 10)

	def test_service_start_from_original_hire(self):
		"""#1・LVR-06 定年後再雇用者は勤続通算起算日からの勤続（6.5年以上）で20日になる。"""
		emp = lf.make_employee("2025-04-01")
		emp.reload()
		emp.jp_service_start_date = "2015-04-01"
		emp.save()
		values = self._plan_values(emp.name, "2025-10-01")
		self.assertEqual((values["service_months"], values["days"]), (126, 20))

	def _plan_values(self, emp, base_date, start=None):
		hire = frappe.db.get_value("Employee", emp, "date_of_joining")
		lf.present_except(emp, start or hire, add_days(base_date, -1))
		return grant_service.build_individual_plan(emp, base_date)

	def test_ratio_by_weekly_hours(self):
		"""#3・LVR-07 週4日・29.5時間は比例付与（7日）、週4日・30時間は通常付与（10日）。"""
		a = lf.make_employee("2025-04-01", weekly_hours_h=29, weekly_hours_m=30, weekly_days=4, **PART).name
		b = lf.make_employee("2025-04-01", weekly_hours_h=30, weekly_days=4, **PART).name
		va, vb = self._plan_values(a, "2025-10-01"), self._plan_values(b, "2025-10-01")
		self.assertEqual((va["category"], va["days"]), ("週4日", 7))
		self.assertEqual((vb["category"], vb["days"]), ("通常", 10))

	def test_annual_days_category(self):
		"""#4・LVR-08 週所定日数が定まらない者は年間所定労働日数で区分する（168日は週3日、169日は週4日）。"""
		expected = {168: ("週3日", 5), 169: ("週4日", 7), 72: ("週1日", 1), 73: ("週2日", 3), 217: ("通常", 10)}
		for days, (cat, grant_days) in expected.items():
			emp = lf.make_employee("2025-04-01", weekly_hours_h=20, weekly_days=0, annual_days=days, annual_days_basis="確定",
				**PART).name
			v = self._plan_values(emp, "2025-10-01")
			self.assertEqual((v["category"], v["days"]), (cat, grant_days), days)

	def test_eighty_percent_boundary(self):
		"""#5・#7・LVR-11・14 全労働日100日で出勤80日は付与、79日は付与なし。経営上の障害による休業日は全労働日から除く。"""
		results = {}
		for absent_count in (20, 21):
			emp = lf.make_employee("2025-04-01").name
			days = lf.workdays("2025-04-01", "2025-09-30")
			excluded = days[: len(days) - 100]
			absent = days[len(days) - 100: len(days) - 100 + absent_count]
			others = {d: ("Absent", None, EMPLOYER) for d in excluded}
			lf.present_except(emp, "2025-04-01", "2025-09-30", absent=absent, others=others)
			v = grant_service.build_individual_plan(emp, "2025-10-01")
			calc = frappe.get_doc(CALC, v["attendance_calc"])
			self.assertEqual(calc.total_working_days, 100)
			self.assertEqual(calc.excluded_days, len(excluded))
			results[absent_count] = (calc.attended_days, v["status"], v["days"])
		self.assertEqual(results[20], (80, "予定", 10))
		self.assertEqual(results[21], (79, "付与なし", 0))

	def test_holiday_work_and_exclusions(self):
		"""#7 所定休日の出勤2日は分母・分子に入れず、経営上の障害による休業3日は全労働日から除く（5.2）。"""
		emp = lf.make_employee("2025-04-01").name
		days = lf.workdays("2025-04-01", "2025-09-30")
		others = {d: ("Absent", None, EMPLOYER) for d in days[:3]}
		lf.present_except(emp, "2025-04-01", "2025-09-30", others=others)
		lf.mark_attendance(emp, ["2025-05-10", "2025-05-17"])  # 土曜の出勤
		v = grant_service.build_individual_plan(emp, "2025-10-01")
		calc = frappe.get_doc(CALC, v["attendance_calc"])
		self.assertEqual(calc.scheduled_days, len(days))
		self.assertEqual(calc.total_working_days, len(days) - 3)
		self.assertEqual(calc.holiday_work_days, 2)
		self.assertEqual(calc.attended_days, len(days) - 3)
		self.assertIn(EMPLOYER, [d.label for d in calc.details if d.kind == "除外"])

	def test_maternity_leave_full_period(self):
		"""#6・LVR-12 産休で算定期間の全日を休業した者の出勤率は100%で付与される。"""
		emp = lf.make_employee("2025-04-01").name
		lf.mark_attendance(emp, lf.workdays("2025-04-01", "2025-09-30"), "On Leave", "産前産後休業")
		v = grant_service.build_individual_plan(emp, "2025-10-01")
		calc = frappe.get_doc(CALC, v["attendance_calc"])
		self.assertEqual((calc.rate, v["status"], v["days"]), (100, "予定", 10))
		self.assertEqual(calc.worked_days, 0)

	def test_no_grant_then_next_year(self):
		"""#8・LVR-14 8割未満の年は「付与なし」として記録し、翌年8割以上なら勤続1.5年の11日（初年度に戻らない）。"""
		emp = lf.make_employee("2025-04-01").name
		days = lf.workdays("2025-04-01", "2025-09-30")
		lf.present_except(emp, "2025-04-01", "2025-09-30", absent=days[:40])
		lf.run_daily("2025-10-01", emp)
		first = plan_of(emp, "2025-10-01")
		self.assertEqual((first.status, first.docstatus, first.days), ("付与なし", 1, 0))
		self.assertIsNone(first.ledger_entry)
		lf.present_except(emp, "2025-10-01", "2026-09-30")
		lf.run_daily("2026-10-01", emp)
		second = plan_of(emp, "2026-10-01")
		self.assertEqual((second.status, second.days, second.service_months), ("確定", 11, 18))
		self.assertEqual(getdate(frappe.db.get_value(CALC, second.attendance_calc, "period_start")), getdate("2025-10-01"))

	def test_base_dates(self):
		"""#9・LVR-05 応答日がない月は末日。8/31 入社は 2/28、2027-08-29 入社は初回 2028-02-29・第2回 2029-02-28。"""
		cases = {"2026-04-01": ["2026-10-01", "2027-10-01"], "2026-04-15": ["2026-10-15", "2027-10-15"],
			"2026-08-28": ["2027-02-28", "2028-02-28"], "2026-08-31": ["2027-02-28", "2028-02-28"],
			"2027-08-29": ["2028-02-29", "2029-02-28"]}
		for hire, expected in cases.items():
			got = [str(d) for _n, d in grant_service.individual_base_dates(getdate(hire), getdate(hire), getdate("2029-12-31"))][:2]
			self.assertEqual(got, expected, hire)
		emp = lf.make_employee("2026-08-31").name
		jobs.create_plans("2027-02-21", lf.emp_rows(emp))
		self.assertIsNotNone(plan_of(emp, "2027-02-28"))

	def test_contract_change_day_before_base_date(self):
		"""#10・LVR-04 基準日の前日に週3日→週4日へ変更すると変更後の区分で付与し、付与済みの分は変わらない。"""
		emp = lf.make_employee("2025-04-01", weekly_hours_h=24, weekly_days=3, **PART).name
		lf.present_except(emp, "2025-04-01", "2025-09-30")
		lf.run_daily("2025-10-01", emp)
		first = plan_of(emp, "2025-10-01")
		self.assertEqual((first.status, first.category, first.days), ("確定", "週3日", 5))
		lf.fx.make_contract(emp, "2026-09-30", weekly_hours_h=28, weekly_days=4, change_reason="条件変更", **PART)
		lf.present_except(emp, "2025-10-01", "2026-09-30")
		lf.run_daily("2026-10-01", emp)
		second = plan_of(emp, "2026-10-01")
		self.assertEqual((second.status, second.category, second.days), ("確定", "週4日", 8))
		first.reload()
		self.assertEqual(first.days, 5)

	def test_grant_table_below_statutory_warning(self):
		"""#11・LVR-03 付与テーブルに法定を下回る行（1.5年を10日）を設定すると警告が表示され、記録が残る。"""
		from japan_hr_compliance.jp_leave.setup import legal_rows

		rows = legal_rows()
		for r in rows:
			if r["category"] == "通常" and r["service_months"] == 18:
				r["days"] = 10
		doc = frappe.get_doc({"doctype": "JP Leave Grant Table", "table_name": f"テスト正社員用{frappe.generate_hash(length=4)}",
			"work_rule_class": "その他", "rows": rows}).insert()
		self.addCleanup(frappe.delete_doc, "JP Leave Grant Table", doc.name, force=True)
		self.assertTrue(doc.below_statutory)
		self.assertIn("勤続18か月", doc.warning_text)
		self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "JP Leave Grant Table", "reference_name": doc.name,
			"content": ("like", "%法定との照合の警告%")}))
		row = next(r for r in doc.rows if r.category == "通常" and r.service_months == 18)
		self.assertEqual((row.legal_days, row.below_legal), (11, 1))

	def test_company_grant_table_by_work_rule(self):
		"""LVR-02・04 就業規則の区分ごとの付与テーブル（法定を上回る正社員用）が基準日時点の契約で選ばれる。"""
		from japan_hr_compliance.jp_leave.setup import legal_rows

		rows = legal_rows()
		for r in rows:
			if r["category"] == "通常" and r["service_months"] == 6:
				r["days"] = 12
		name = f"テスト正社員用{frappe.generate_hash(length=4)}"
		frappe.get_doc({"doctype": "JP Leave Grant Table", "table_name": name, "work_rule_class": "正社員用",
			"company": lf.fx.COMPANY, "rows": rows}).insert()
		self.addCleanup(frappe.delete_doc, "JP Leave Grant Table", name, force=True)
		emp = lf.make_employee("2025-04-01").name
		v = self._plan_values(emp, "2025-10-01")
		self.assertEqual((v["grant_table"], v["days"]), (name, 12))

	def test_shift_worker_actual_days(self):
		"""#25・LVR-08 シフト制のパート（目安60日）の初回は、6か月の労働日数の実績60日×2＝120日で「週2日」区分。"""
		emp = lf.make_employee("2025-04-01", weekly_hours_h=16, weekly_days=0, annual_days=60, annual_days_basis="目安", **PART).name
		work = lf.workdays("2025-04-01", "2025-09-30")[::2][:60]
		hl = frappe.get_doc({"doctype": "Holiday List", "holiday_list_name": f"テストシフト{frappe.generate_hash(length=5)}",
			"from_date": "2025-01-01", "to_date": "2026-12-31"})
		d = getdate("2025-01-01")
		work_set = set(work)
		while d <= getdate("2026-12-31"):
			if d not in work_set:
				hl.append("holidays", {"holiday_date": d, "description": "シフトなし"})
			d = add_days(d, 1)
		hl.insert()
		hla = frappe.get_doc({"doctype": "Holiday List Assignment", "applicable_for": "Employee", "assigned_to": emp,
			"holiday_list": hl.name, "from_date": "2025-04-01"}).insert()
		hla.submit()
		lf.mark_attendance(emp, work)
		v = grant_service.build_individual_plan(emp, "2025-10-01")
		self.assertEqual((v["annual_days"], v["annual_days_basis"], v["category"], v["days"]), (120, "実績", "週2日", 3))
		self.assertIn("実績120日", v["category_basis"])

	def test_pending_overdue_notified_daily(self):
		"""#26・LVR-16・20 勤怠未確定の従業員は確認待ちのまま基準日を過ぎると、付与漏れとして毎日通知される。"""
		emp = lf.make_employee("2025-04-01").name
		lf.present_except(emp, "2025-04-01", "2025-09-15")  # 9/16 以降が未入力
		lf.run_daily("2025-10-01", emp)
		plan = plan_of(emp, "2025-10-01")
		self.assertEqual((plan.status, plan.docstatus), ("保留", 0))
		self.assertIn("勤怠が未確定", plan.pending_reasons)
		filters = {"document_name": plan.name, "subject": ("like", "%付与漏れ%")}
		self.assertEqual(frappe.db.count("Notification Log", filters), 0)
		lf.run_daily("2025-10-02", emp)
		n1 = frappe.db.count("Notification Log", filters)
		lf.run_daily("2025-10-03", emp)
		self.assertGreater(n1, 0)
		self.assertEqual(frappe.db.count("Notification Log", filters), n1 * 2)
		# 勤怠が揃えば翌日の処理で確定する
		lf.present_except(emp, "2025-09-16", "2025-09-30")
		lf.run_daily("2025-10-04", emp)
		plan.reload()
		self.assertEqual((plan.status, plan.days), ("確定", 10))

	def test_confirm_all_manually(self):
		"""LVR-20 会社設定で全件を担当者確認にすると、基準日に自動確定しない。"""
		lf.ensure_settings(confirm_method="全件確認")
		emp = lf.make_employee("2025-04-01").name
		lf.present_except(emp, "2025-04-01", "2025-09-30")
		lf.run_daily("2025-10-01", emp)
		plan = plan_of(emp, "2025-10-01")
		self.assertEqual((plan.status, plan.docstatus), ("予定", 0))
		plan.submit()
		self.assertEqual(plan.status, "確定")

	def test_service_unconfirmed_is_pending(self):
		"""LVR-20 勤続通算が未確定の従業員は確認待ちになる。"""
		emp = lf.make_employee("2025-04-01")
		emp.reload()
		emp.jp_service_start_confirmed = 0
		emp.save()
		v = self._plan_values(emp.name, "2025-10-01")
		self.assertEqual(v["status"], "保留")
		self.assertIn("勤続通算", v["pending_reasons"])

	def test_manual_adjustment_requires_reason(self):
		"""LVR-24 手動の付与・調整は理由が必須で、理由・実行者・日時が履歴に残る。"""
		emp = lf.make_employee("2025-04-01").name
		with self.assertRaises(frappe.ValidationError):
			lf.grant(emp, "2025-12-01", 2, kind="調整", reason="")
		g = lf.grant(emp, "2025-12-01", 2, kind="調整", reason="誤りの訂正（追加付与）")
		self.assertEqual(g.grant_kind, "調整")
		self.assertTrue(frappe.db.exists("Comment", {"reference_name": g.name, "content": ("like", "%誤りの訂正%")}))

	def test_cancel_grant(self):
		"""LVR-25 充当のない付与は取消でき（器に負の行）、充当のある付与は止まる。"""
		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		lf.apply_leave(emp, "2025-11-04")
		with self.assertRaises(frappe.ValidationError):
			g.cancel()
		emp2 = lf.make_employee("2025-04-01").name
		g2 = lf.grant(emp2, "2025-10-01", 10)
		g2.cancel()
		self.assertEqual(frappe.db.get_value(GRANT, g2.name, "status"), "取消")
		self.assertBalance(emp2, "2025-10-02", 0)

	def test_settings_validation(self):
		"""LVR-30・61 有効期間は2年未満、保存年数は3年未満にできない。"""
		with self.assertRaises(frappe.ValidationError):
			lf.ensure_settings(validity_years=1)
		with self.assertRaises(frappe.ValidationError):
			lf.ensure_settings(retention_years=2)

	def test_longer_validity(self):
		"""LVR-30 会社設定で法定より長い有効期間（3年）にすると最終使用日が3年後の前日になる。"""
		lf.ensure_settings(validity_years=3)
		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		self.assertEqual(getdate(g.expiry_date), getdate("2028-09-30"))

	def test_treatment_snapshot_in_calc(self):
		"""LVR-13・15 法令に定めのない休暇の扱いが算定明細に写される。"""
		emp = lf.make_employee("2025-04-01").name
		days = lf.workdays("2025-04-01", "2025-09-30")
		lf.present_except(emp, "2025-04-01", "2025-09-30", others={days[0]: ("On Leave", "生理休暇", None)})
		v = grant_service.build_individual_plan(emp, "2025-10-01")
		calc = frappe.get_doc(CALC, v["attendance_calc"])
		self.assertEqual(json.loads(calc.treatments), {"生理休暇": "出勤とみなす"})
