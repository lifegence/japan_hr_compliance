"""hrms 連携（3.1 の方式）の検証と、時効・充当のテスト（B-3 7章 観点 #12〜#15、LVR-30〜33）。

3.1 の検証項目
- 残高計算：hrms の get_leave_balance_on が各時点で年休付与の残日数の合計と一致する（#12）
- Leave Balance レポート：期間の期首残高＋付与－取得＝期末残高が hrms の残高と一致する
- 失効ジョブ：hrms の process_expired_allocation が器を失効させない
"""

import frappe
from frappe.utils import getdate

from japan_hr_compliance.jp_leave import container, jobs
from japan_hr_compliance.jp_leave.utils import ANNUAL_LEAVE_TYPE, CONSUMPTION, GRANT
from japan_hr_compliance.tests import leave_fixtures as lf
from japan_hr_compliance.tests.base import JPHRTestCase

LT = ANNUAL_LEAVE_TYPE


class LeaveTestCase(JPHRTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		lf.ensure_all()

	def setUp(self):
		lf.ensure_settings()

	def tearDown(self):
		frappe.flags.jp_leave_today = None
		super().tearDown()

	def assertBalance(self, employee, on, expected):
		"""年休付与の残日数の合計と hrms の残高がどちらも expected（LVR-32）。"""
		ours = sum(frappe.get_all(GRANT, filters={"employee": employee, "docstatus": 1, "status": "確定",
			"grant_date": ("<=", on)}, pluck="remaining_days"))
		self.assertEqual(ours, expected, f"年休付与の残日数の合計（{on}）")
		self.assertEqual(container.hrms_balance(employee, LT, on), expected, f"hrms の残高（{on}）")


class TestHrmsIntegration(LeaveTestCase):
	def test_scenario_5_4_matches_hrms(self):
		"""#12 規則 5.4：取得・付与・複数の付与分にまたがる取得・失効の各時点で、残日数が表どおりで hrms と一致する。"""
		emp = lf.make_employee("2026-04-01").name
		g1 = lf.grant(emp, "2026-10-01", 10)
		self.assertEqual(getdate(g1.expiry_date), getdate("2028-09-30"))
		self.assertBalance(emp, "2026-10-01", 10)
		alloc = frappe.get_doc("Leave Allocation", g1.leave_allocation)
		self.assertTrue(alloc.jp_leave_container)
		self.assertEqual(getdate(alloc.from_date), getdate("2026-10-01"))

		lf.apply_leave(emp, "2027-06-10", "2027-06-14")  # 木・金・月の3日
		self.assertBalance(emp, "2027-06-14", 7)

		g2 = lf.grant(emp, "2027-10-01", 11)
		self.assertEqual(g2.leave_allocation, g1.leave_allocation, "器は従業員ごとに1本")
		self.assertBalance(emp, "2027-10-01", 18)

		lf.apply_leave(emp, "2027-12-20", "2027-12-24")
		self.assertEqual(lf.remaining(g1.name), 2)
		self.assertEqual(lf.remaining(g2.name), 11)
		self.assertBalance(emp, "2027-12-24", 13)

		la = lf.apply_leave(emp, "2028-09-27", "2028-09-29")  # ①から2日、②から1日
		rows = frappe.get_all(CONSUMPTION, filters={"leave_application": la.name}, fields=["grant", "days"], order_by="leave_date")
		self.assertEqual([(r.grant, r.days) for r in rows], [(g1.name, 1), (g1.name, 1), (g2.name, 1)])
		self.assertBalance(emp, "2028-09-29", 10)

		lf.run_daily("2028-10-01")
		self.assertEqual(frappe.db.get_value(GRANT, g1.name, "status"), "失効")
		g3 = lf.grant(emp, "2028-10-01", 12)
		self.assertBalance(emp, "2028-10-01", 22)

		lf.run_daily("2029-10-01")
		self.assertEqual(frappe.db.get_value(GRANT, g2.name, ["status", "lapsed_days"]), ("失効", 10))
		lf.grant(emp, "2029-10-01", 14)
		self.assertBalance(emp, "2029-10-01", 26)
		self.assertEqual(lf.remaining(g3.name), 12)
		self.assertFalse([d for d in jobs.reconcile("2029-10-01") if d.employee == emp])

		# Leave Balance レポート：期首＋付与－失効－取得＝期末が hrms の残高と一致する
		from hrms.hr.report.employee_leave_balance.employee_leave_balance import execute

		_cols, data = execute(frappe._dict(from_date="2027-10-01", to_date="2028-12-31", company=lf.fx.COMPANY, employee=emp))[:2]
		row = next(r for r in data if r.get("leave_type") == LT)
		self.assertEqual(row["opening_balance"], 7)
		self.assertEqual(row["leaves_taken"], 8)
		self.assertEqual(row["closing_balance"], 22)
		_cols, data = execute(frappe._dict(from_date="2029-01-01", to_date="2029-12-31", company=lf.fx.COMPANY, employee=emp))[:2]
		row = next(r for r in data if r.get("leave_type") == LT)
		self.assertEqual((row["opening_balance"], row["closing_balance"]), (22, 26))

	def test_hrms_expiry_job_does_not_expire_container(self):
		"""hrms の失効ジョブ（process_expired_allocation）は器を失効させない（3.1 の検証）。"""
		from hrms.hr.doctype.leave_ledger_entry.leave_ledger_entry import process_expired_allocation

		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		before = frappe.db.count("Leave Ledger Entry", {"transaction_name": g.leave_allocation})
		process_expired_allocation()
		self.assertEqual(frappe.db.count("Leave Ledger Entry", {"transaction_name": g.leave_allocation}), before)
		self.assertFalse(frappe.db.get_value("Leave Allocation", g.leave_allocation, "expired"))

	def test_container_cannot_be_cancelled(self):
		"""器の Leave Allocation は確定済みの年休付与がある間は取り消せない（台帳行がすべて消えるため）。"""
		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		alloc = frappe.get_doc("Leave Allocation", g.leave_allocation)
		with self.assertRaises(frappe.ValidationError):
			alloc.cancel()

	def test_expiry_day_before_on_after(self):
		"""#13 失効日の前日・当日は古い付与分から充当、翌日以降は失効済み分を使わない（LVR-30・31）。"""
		emp = lf.make_employee("2025-04-01").name
		old = lf.grant(emp, "2026-06-03", 3, kind="調整", reason="移行時の残日数")
		self.assertEqual(getdate(old.expiry_date), getdate("2028-06-02"))  # 金曜
		new = lf.grant(emp, "2027-06-03", 5, kind="調整", reason="移行時の残日数")
		for day in ("2028-06-01", "2028-06-02"):
			la = lf.apply_leave(emp, day)
			self.assertEqual(frappe.db.get_value(CONSUMPTION, {"leave_application": la.name}, "grant"), old.name, day)
		la = lf.apply_leave(emp, "2028-06-05")
		self.assertEqual(frappe.db.get_value(CONSUMPTION, {"leave_application": la.name}, "grant"), new.name)
		# 古い付与分に残り1日があっても、最終使用日の翌日以降の申請には使えない
		self.assertEqual(lf.remaining(old.name), 1)

	def test_insufficient_after_expiry(self):
		"""#13 最終使用日の後の日は、失効予定の付与分しかなければ残日数不足になる。"""
		emp = lf.make_employee("2025-04-01").name
		lf.grant(emp, "2026-06-03", 3, kind="調整", reason="移行時の残日数")
		with self.assertRaises(frappe.ValidationError):
			lf.apply_leave(emp, "2028-06-05")

	def test_cancel_across_expiry_not_restored(self):
		"""#14 失効日をまたいで取り消された取得は失効済みの付与分に戻らず、担当者に通知する（LVR-33）。"""
		emp = lf.make_employee("2025-04-01").name
		old = lf.grant(emp, "2026-06-03", 3, kind="調整", reason="移行時の残日数")
		new = lf.grant(emp, "2027-06-03", 5, kind="調整", reason="移行時の残日数")
		la = lf.apply_leave(emp, "2028-06-01")
		lf.run_daily("2028-06-03")
		self.assertEqual(frappe.db.get_value(GRANT, old.name, ["status", "lapsed_days"]), ("失効", 2))
		frappe.flags.jp_leave_today = getdate("2028-06-05")
		la.reload()
		la.cancel()
		c = frappe.db.get_value(CONSUMPTION, {"leave_application": la.name}, ["cancelled", "not_restored"], as_dict=True)
		self.assertEqual((c.cancelled, c.not_restored), (1, 1))
		self.assertEqual(frappe.db.get_value(GRANT, old.name, ["remaining_days", "lapsed_days"]), (0, 3))
		self.assertBalance(emp, "2028-06-05", 5)
		self.assertEqual(lf.remaining(new.name), 5)
		self.assertTrue(frappe.db.exists("Notification Log", {"document_name": la.name, "subject": ("like", "%失効済み%")}))

	def test_cancel_restores_active_grant(self):
		"""取得の取消で、有効な付与分に充当を戻す（LVR-33）。"""
		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		la = lf.apply_leave(emp, "2025-12-01", "2025-12-03")
		self.assertEqual(lf.remaining(g.name), 7)
		la.reload()
		la.cancel()
		self.assertEqual(lf.remaining(g.name), 10)
		self.assertBalance(emp, "2025-12-03", 10)

	def test_application_across_base_date(self):
		"""#15 基準日をまたぐ連続5日の申請を1件で受け付け、付与日前の日は古い付与分から充当する。"""
		emp = lf.make_employee("2026-04-01").name
		g1 = lf.grant(emp, "2026-10-01", 10)
		lf.apply_leave(emp, "2027-01-04", "2027-01-13")  # 8日
		g2 = lf.grant(emp, "2027-10-01", 11)
		la = lf.apply_leave(emp, "2027-09-29", "2027-10-05")  # 9/29・9/30・10/1・10/4・10/5
		self.assertEqual(la.total_leave_days, 5)
		rows = frappe.get_all(CONSUMPTION, filters={"leave_application": la.name}, fields=["grant", "leave_date"],
			order_by="leave_date")
		self.assertEqual([r.grant for r in rows], [g1.name, g1.name, g2.name, g2.name, g2.name])
		self.assertEqual(getdate(rows[2].leave_date), getdate("2027-10-01"))
		self.assertEqual((lf.remaining(g1.name), lf.remaining(g2.name)), (0, 8))
		self.assertBalance(emp, "2027-10-05", 8)

	def test_half_day_records_part(self):
		"""#19・LVR-40 半日取得で残日数が0.5日減り、午前・午後が記録される。"""
		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		with self.assertRaises(frappe.ValidationError):
			lf.apply_leave(emp, "2025-11-04", half_day=1)  # 午前・午後の指定なし
		la = lf.apply_leave(emp, "2025-11-04", half_day=1, part="午後")
		self.assertEqual(lf.remaining(g.name), 9.5)
		c = frappe.db.get_value(CONSUMPTION, {"leave_application": la.name}, ["days", "half_day_part"], as_dict=True)
		self.assertEqual((c.days, c.half_day_part), (0.5, "午後"))
		self.assertBalance(emp, "2025-11-04", 9.5)

	def test_annual_leave_type_is_paid(self):
		"""#23・LVR-43 年休の休暇種別は無給にしない（欠勤控除の対象外）。無給への変更はエラー。"""
		lt = frappe.get_doc("Leave Type", LT)
		self.assertEqual(lt.is_lwp, 0)
		lt.is_lwp = 1
		with self.assertRaises(frappe.ValidationError):
			lt.save()

	def test_hrms_standard_check_rejects_across_base_date(self):
		"""3.1 の検証：hrms 標準の残高チェックだけでは #15 の申請が「残高不足」で拒否される（開始日時点の残高で判定するため）。
		このため年休の休暇種別は allow_negative=1 とし、付与単位の残高検査で止める。"""
		from hrms.hr.doctype.leave_application.leave_application import InsufficientLeaveBalanceError

		emp = lf.make_employee("2026-04-01").name
		lf.grant(emp, "2026-10-01", 10)
		lf.apply_leave(emp, "2027-01-04", "2027-01-13")
		lf.grant(emp, "2027-10-01", 11)
		frappe.db.set_value("Leave Type", LT, "allow_negative", 0)
		frappe.clear_cache(doctype="Leave Type")
		try:
			with self.assertRaises(InsufficientLeaveBalanceError):
				lf.apply_leave(emp, "2027-09-29", "2027-10-05", submit=False)
		finally:
			frappe.db.set_value("Leave Type", LT, "allow_negative", 1)
			frappe.clear_cache(doctype="Leave Type")

	def test_hrms_check_alone_misses_expiry(self):
		"""3.1 の検証：失効予定の付与分しかない日の申請は hrms 標準の残高では通るが、付与単位の残高検査で止まる。"""
		from japan_hr_compliance.jp_leave.consumption import load_ledger, take_days

		emp = lf.make_employee("2025-04-01").name
		lf.grant(emp, "2026-06-03", 3, kind="調整", reason="移行時の残日数")
		self.assertEqual(container.hrms_balance(emp, LT, "2028-06-05"), 3, "hrms は失効処理の前は残高に数える")
		la = frappe.get_doc({"doctype": "Leave Application", "employee": emp, "leave_type": LT, "from_date": "2028-06-05",
			"to_date": "2028-06-05", "company": lf.fx.COMPANY})
		self.assertEqual(load_ledger(emp).balance(getdate("2028-06-05")), 0)
		self.assertEqual(len(take_days(la)), 1)
