"""斉一的付与・前倒し・分割付与と年5日の取得義務のテスト（B-3 7章 観点 #16〜#21・#28、LVR-01・21〜23・41・50〜54）。"""

import frappe

from japan_hr_compliance.jp_leave import five_day_service, jobs
from japan_hr_compliance.jp_leave.utils import CALC, CONSUMPTION, GRANT, OBLIGATION
from japan_hr_compliance.tests import leave_fixtures as lf
from japan_hr_compliance.tests.test_leave_hrms import LeaveTestCase

PART = dict(employment_class="パートタイマー", work_rule_class="パートタイマー用", wage_type="時給", daily_hours_h=8, monthly_days=13)


def obligations(emp):
	return frappe.get_all(OBLIGATION, filters={"employee": emp, "status": ("!=", "無効")},
		fields=["name", "period_start", "period_end", "count_from", "required_days", "months", "taken_days", "is_prorated"],
		order_by="period_start asc")


class TestAdvanceAndFiveDay(LeaveTestCase):
	def uniform(self, **kw):
		return lf.ensure_settings(grant_method="斉一的付与", uniform_base_dates=[(4, 1)], **kw)

	def test_uniform_manual_check_required(self):
		"""#16・LVR-22 前倒し付与は確認チェックなしでは確定できず、ありなら確定して確認者・日時が残る。"""
		self.uniform()
		emp = lf.make_employee("2026-10-01").name
		g = lf.grant(emp, "2026-10-01", 10, round_no=1, checked=0, submit=False)
		self.assertEqual((g.grant_kind, str(g.legal_base_date), g.legal_days, g.advance_period), ("前倒し", "2027-04-01", 10, "6か月"))
		self.assertTrue(g.manual_check_items)
		with self.assertRaises(frappe.ValidationError):
			g.submit()
		g.reload()
		g.manual_checked = 1
		g.save()
		g.submit()
		self.assertEqual((g.status, g.checked_by), ("確定", "Administrator"))
		self.assertTrue(g.checked_on)
		g2 = lf.grant(emp, "2027-04-01", 11, round_no=2)
		self.assertEqual((g2.grant_kind, str(g2.legal_base_date), g2.advance_period), ("前倒し", "2028-04-01", "12か月"))

	def test_uniform_shorter_advance_is_error(self):
		"""#16・LVR-22 ② 2回目の繰上げ（3か月）が初年度（6か月）より短い付与日はエラーで確定できない。"""
		self.uniform()
		emp = lf.make_employee("2026-10-01").name
		lf.grant(emp, "2026-10-01", 10, round_no=1)
		with self.assertRaises(frappe.ValidationError):
			lf.grant(emp, "2028-01-01", 11, round_no=2)

	def test_uniform_auto_plan_with_shortened_period(self):
		"""LVR-01・22 ① 斉一的付与の会社では全社基準日の付与予定を作り、短縮された期間は出勤とみなす。確認待ちで止まる。"""
		self.uniform()
		emp = lf.make_employee("2026-10-01").name
		lf.grant(emp, "2026-10-01", 10, round_no=1)
		lf.present_except(emp, "2026-10-01", "2027-03-31")
		lf.run_daily("2027-04-01", emp)
		plan = frappe.get_doc(GRANT, {"employee": emp, "grant_date": "2027-04-01"})
		self.assertEqual((plan.round_no, plan.days, plan.grant_kind, plan.docstatus), (2, 11, "前倒し", 0))
		self.assertEqual(plan.status, "保留")
		self.assertIn("確認", plan.pending_reasons)
		calc = frappe.get_doc(CALC, plan.attendance_calc)
		self.assertEqual((str(calc.period_start), str(calc.period_end)), ("2026-10-01", "2027-03-31"))
		self.assertEqual(calc.shortened_deemed_days, len(lf.workdays("2027-04-01", "2028-03-31")))
		plan.manual_checked = 1
		plan.save()
		plan.submit()
		self.assertEqual(plan.status, "確定")

	def test_split_grant_ten_day_reach(self):
		"""#17・LVR-23 分割付与（入社時5日・6か月後5日）は2回目が10日到達日になり、年5日の期間の起点になる。"""
		emp = lf.make_employee("2026-04-01").name
		first = lf.grant(emp, "2026-04-01", 5, round_no=1)
		self.assertEqual((first.five_day_target, first.ten_day_reach_date), (0, None))
		self.assertIn("分割付与の途中", first.judgement)
		self.assertEqual(obligations(emp), [], "1回目（5日）では年5日の期間を作らない")
		lf.apply_leave(emp, "2026-06-01")
		second = lf.grant(emp, "2026-10-01", 5)
		self.assertEqual((second.round_no, second.grant_kind, second.cumulative_days), (1, "分割", 10))
		self.assertEqual(str(second.ten_day_reach_date), "2026-10-01")
		self.assertEqual(frappe.db.get_value(GRANT, first.name, "grant_kind"), "分割")
		obs = obligations(emp)
		self.assertEqual(len(obs), 1)
		self.assertEqual((str(obs[0].period_start), str(obs[0].period_end), str(obs[0].count_from)),
			("2026-10-01", "2027-09-30", "2026-04-01"))
		five_day_service.update_taken(emp, "2026-10-01")
		self.assertEqual(frappe.db.get_value(OBLIGATION, obs[0].name, "taken_days"), 1, "10日到達日より前の取得を含める")

	def test_split_short_after_legal_base_is_error(self):
		"""#17・LVR-22 ③ 法定の基準日までに法定日数に満たない分割付与（2回目が基準日の後）はエラー。"""
		emp = lf.make_employee("2026-04-01").name
		lf.grant(emp, "2026-04-01", 5, round_no=1)
		with self.assertRaises(frappe.ValidationError):
			lf.grant(emp, "2026-10-02", 5, round_no=1)

	def test_double_track_prorate_and_each(self):
		"""#18・LVR-51 ダブルトラック（5.6）：按分は18か月・7.5日、各期間方式は5日ずつ。管理簿に基準日が2つ出る。"""
		self.uniform()
		emp = lf.make_employee("2026-10-01").name
		lf.grant(emp, "2026-10-01", 10, round_no=1)
		lf.grant(emp, "2027-04-01", 11, round_no=2)
		obs = obligations(emp)
		self.assertEqual(len(obs), 1)
		self.assertEqual((str(obs[0].period_start), str(obs[0].period_end), obs[0].months, obs[0].required_days, obs[0].is_prorated),
			("2026-10-01", "2028-03-31", 18, 7.5, 1))
		from japan_hr_compliance.jp_leave.report.jp_annual_leave_register.jp_annual_leave_register import get_data

		rows = get_data(frappe._dict(company=lf.fx.COMPANY, employee=emp, from_date="2026-01-01", to_date="2028-12-31"))
		self.assertEqual([(str(r["base_date"]), str(r["base_date_2"])) for r in rows],
			[("2026-10-01", "2027-04-01"), ("2027-04-01", "2026-10-01")])
		self.uniform(overlap_method="各期間で5日")
		five_day_service.rebuild(emp)
		obs = obligations(emp)
		self.assertEqual([(str(o.period_start), str(o.period_end), o.required_days) for o in obs],
			[("2026-10-01", "2027-09-30", 5), ("2027-04-01", "2028-03-31", 5)])

	def test_double_track_without_half_day(self):
		"""#28・LVR-51 半日取得なしの会社でダブルトラック（按分）は必要日数が8日。"""
		self.uniform(half_day_allowed=0)
		emp = lf.make_employee("2026-10-01").name
		lf.grant(emp, "2026-10-01", 10, round_no=1)
		lf.grant(emp, "2027-04-01", 11, round_no=2)
		self.assertEqual(obligations(emp)[0].required_days, 8)
		with self.assertRaises(frappe.ValidationError):
			lf.apply_leave(emp, "2027-05-06", half_day=1, part="午前")

	def test_ratio_nine_and_ten_days(self):
		"""#20・LVR-50 比例付与で10日（週4日・3.5年）の者は年5日の対象、9日（週4日・2.5年）の者は対象外。"""
		a = lf.make_employee("2022-04-01", weekly_hours_h=29, weekly_days=4, **PART).name
		b = lf.make_employee("2023-04-01", weekly_hours_h=29, weekly_days=4, **PART).name
		ga = lf.grant(a, "2025-10-01", 10)
		gb = lf.grant(b, "2025-10-01", 9)
		self.assertEqual((ga.round_no, ga.legal_days, ga.five_day_target), (4, 10, 1))
		self.assertEqual((gb.round_no, gb.legal_days, gb.five_day_target), (3, 9, 0))
		self.assertEqual(len(obligations(a)), 1)
		self.assertEqual(obligations(b), [])

	def test_counted_days(self):
		"""#19・LVR-52・74 年5日の取得日数は半日0.5日、1日1日で数え、特別休暇は数えない。"""
		if not frappe.db.exists("Leave Type", "テスト慶弔休暇"):
			frappe.get_doc({"doctype": "Leave Type", "leave_type_name": "テスト慶弔休暇", "is_lwp": 1,
				"jp_leave_category": "特別休暇"}).insert()
		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		lf.apply_leave(emp, "2025-11-04", half_day=1, part="午前")
		lf.apply_leave(emp, "2025-11-05")
		lf.apply_leave(emp, "2025-11-06", leave_type="テスト慶弔休暇")
		ob = obligations(emp)[0]
		five_day_service.update_taken(emp, "2025-12-01")
		self.assertEqual(frappe.db.get_value(OBLIGATION, ob.name, "taken_days"), 1.5)
		self.assertEqual(frappe.db.get_value(GRANT, g.name, "remaining_days"), 8.5, "特別休暇は年休の残日数に影響しない")

	def test_notices_three_stages(self):
		"""#21・LVR-53 6か月・9か月・期間終了の2か月前に、未取得の日数が残る従業員が一覧に出て通知が送られる。"""
		emp = lf.make_employee("2026-04-01").name
		done = lf.make_employee("2026-04-01").name
		lf.grant(emp, "2026-10-01", 10)
		lf.grant(done, "2026-10-01", 10)
		lf.apply_leave(done, "2027-01-04", "2027-01-08")
		ob = obligations(emp)[0].name
		self.assertFalse([x for x in five_day_service.send_notices("2027-03-31") if x[0] == ob])
		sent = five_day_service.send_notices("2027-04-01")
		self.assertIn((ob, "早期注意"), sent)
		self.assertFalse([x for x in sent if x[0] == obligations(done)[0].name], "取得済みの者には通知しない")
		self.assertIn(emp, [r.employee for r in five_day_service.shortfall_list("2027-04-01")])
		self.assertNotIn(done, [r.employee for r in five_day_service.shortfall_list("2027-04-01")])
		self.assertIn((ob, "要対応"), five_day_service.send_notices("2027-07-01"))
		self.assertIn((ob, "緊急"), five_day_service.send_notices("2027-07-30"))
		doc = frappe.get_doc(OBLIGATION, ob)
		self.assertEqual([n.stage for n in doc.notices], ["早期注意", "要対応", "緊急"])
		self.assertIn(lf.HR_USER, doc.notices[0].recipients)
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": lf.HR_USER, "document_name": ob,
			"subject": ("like", "%緊急%")}))
		self.assertFalse([x for x in five_day_service.send_notices("2027-08-01") if x[0] == ob], "同じ段階は1回だけ")

	def test_designation_creates_leave(self):
		"""LVR-54 時季指定の記録から年休の申請（区分：時季指定）が作成され、年5日に数えられ、管理簿に区分が出る。"""
		emp = lf.make_employee("2026-04-01").name
		lf.grant(emp, "2026-10-01", 10)
		ob = obligations(emp)[0].name
		d = frappe.get_doc({"doctype": "JP Leave Designation", "employee": emp, "obligation": ob, "hearing_date": "2027-07-01",
			"hearing_method": "面談", "employee_wish": "8月の第1週を希望", "designated_date": "2027-08-02"}).insert()
		d.submit()
		la = frappe.get_doc("Leave Application", d.leave_application)
		self.assertEqual((la.docstatus, la.jp_take_kind, la.status), (1, "時季指定", "Approved"))
		self.assertEqual(frappe.db.get_value(CONSUMPTION, {"leave_application": la.name}, "take_kind"), "時季指定")
		five_day_service.update_taken(emp, "2027-08-02")
		self.assertEqual(frappe.db.get_value(OBLIGATION, ob, "taken_days"), 1)
		from japan_hr_compliance.jp_leave.report.jp_annual_leave_register.jp_annual_leave_register import get_data

		rows = get_data(frappe._dict(company=lf.fx.COMPANY, employee=emp, from_date="2026-01-01", to_date="2028-12-31"))
		self.assertIn("2027-08-02（1日・時季指定）", rows[0]["takes"])
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "JP Leave Designation", "employee": emp, "obligation": ob, "hearing_date": "2027-07-01",
				"hearing_method": "面談", "designated_date": "2027-10-04"}).insert()

	def test_bulk_planned_leave_and_grant(self):
		"""LVR-21・41 対象者一括の付与と計画的付与の登録。残日数のない対象者は結果にエラーが残る。"""
		a = lf.make_employee("2025-04-01").name
		b = lf.make_employee("2025-04-01").name
		bulk = frappe.get_doc({"doctype": "JP Leave Bulk Entry", "purpose": "付与", "company": lf.fx.COMPANY,
			"grant_date": "2025-10-01", "reason": "個別基準日の付与（移行）", "employees": [{"employee": a}]}).insert()
		bulk.submit()
		bulk.reload()
		self.assertTrue(bulk.employees[0].reference)
		self.assertEqual(frappe.db.get_value(GRANT, bulk.employees[0].reference, ["days", "status"]), (10, "確定"))
		plan = frappe.get_doc({"doctype": "JP Leave Bulk Entry", "purpose": "計画的付与", "company": lf.fx.COMPANY,
			"from_date": "2025-12-29", "to_date": "2025-12-30", "reason": "年末の計画的付与",
			"employees": [{"employee": a}, {"employee": b}]}).insert()
		plan.submit()
		plan.reload()
		ok, ng = plan.employees
		self.assertEqual(frappe.db.get_value("Leave Application", ok.reference, ["jp_take_kind", "total_leave_days"]),
			("計画的付与", 2))
		self.assertIn("エラー", ng.result)
		self.assertFalse(ng.reference)

	def test_no_obligation_for_adjustment(self):
		"""LVR-50 調整（追加付与）は年5日の回の累計に含めない。"""
		emp = lf.make_employee("2025-04-01").name
		lf.grant(emp, "2025-12-01", 10, kind="調整", reason="移行時の残日数")
		self.assertEqual(obligations(emp), [])
		jobs.reconcile("2025-12-01")
