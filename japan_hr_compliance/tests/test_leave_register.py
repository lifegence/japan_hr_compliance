"""管理簿・保存・退職・法定休暇・ポータルのテスト（B-3 7章 観点 #2・#22・#24・#27、LVR-06・13・34・60〜63・70〜74）。"""

import frappe
from frappe.utils import getdate

from japan_hr_compliance.jp_leave import container, leave_rules
from japan_hr_compliance.jp_leave.calc.grant_table import LEGAL_DAYS
from japan_hr_compliance.jp_leave.report.jp_annual_leave_register import jp_annual_leave_register as register
from japan_hr_compliance.jp_leave.setup import LEAVE_TYPES, LEGAL_TABLE_NAME
from japan_hr_compliance.jp_leave.utils import CONSUMPTION, GRANT
from japan_hr_compliance.tests import leave_fixtures as lf
from japan_hr_compliance.tests.test_leave_hrms import LT, LeaveTestCase


class TestLeaveRegister(LeaveTestCase):
	def test_initial_data(self):
		"""#2・LVR-02・70・71 法定の付与表（通常・比例付与の35行）と法定休暇の休暇種別が初期データにある。"""
		table = frappe.get_doc("JP Leave Grant Table", LEGAL_TABLE_NAME)
		self.assertEqual(len(table.rows), 35)
		self.assertFalse(table.below_statutory)
		for cat, days in LEGAL_DAYS.items():
			got = [r.days for r in sorted((r for r in table.rows if r.category == cat.value), key=lambda r: r.service_months)]
			self.assertEqual(got, list(days), cat.value)
		for name, category, kind, lwp, treatment, si, basis in LEAVE_TYPES:
			lt = frappe.get_doc("Leave Type", name)
			self.assertEqual((lt.jp_leave_category, lt.jp_statutory_kind or "", lt.jp_statutory_basis), (category, kind, basis))
		self.assertTrue(frappe.db.get_value("Leave Type", "育児休業", "jp_si_exemption"))
		self.assertTrue(frappe.db.exists("JP Leave Settings", lf.fx.COMPANY))

	def test_register_output_and_files(self):
		"""#22・LVR-60・62 管理簿に基準日・日数・時季（取得日・区分）が出力され、PDF・Excel で出力できる。"""
		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		lf.apply_leave(emp, "2025-11-04", half_day=1, part="午前")
		lf.apply_leave(emp, "2025-11-05", "2025-11-06")
		rows = register.get_data(frappe._dict(company=lf.fx.COMPANY, employee=emp, from_date="2023-01-01", to_date="2026-11-15"))
		self.assertEqual(len(rows), 1)
		r = rows[0]
		self.assertEqual((str(r["base_date"]), r["days"], r["taken_days"], r["remaining_days"], r["grant"]),
			("2025-10-01", 10, 2.5, 7.5, g.name))
		self.assertIn("2025-11-04（半日・午前・本人の請求）", r["takes"])
		self.assertIn("2025-11-06（1日・本人の請求）", r["takes"])
		self.assertEqual(r["five_day_period"], "2025-10-01〜2026-09-30")
		columns, data = register.execute({"company": lf.fx.COMPANY, "employee": emp, "from_date": "2023-11-15", "to_date": "2026-11-15"})
		self.assertEqual(len(data), 1)
		self.assertIn("基準日（付与日）", [c["label"] for c in columns])
		xlsx = register.build_xlsx({"company": lf.fx.COMPANY, "employee": emp, "from_date": "2023-11-15"})
		self.assertEqual(xlsx[:2], b"PK")
		pdf = register.build_pdf({"company": lf.fx.COMPANY, "employee": emp, "from_date": "2023-11-15"})
		self.assertEqual(pdf[:4], b"%PDF")

	def test_retention_blocks_delete(self):
		"""#22・LVR-61 保存期間内の管理簿の元データ（年休付与・年休充当・算定明細）は管理者でも削除できない。"""
		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		lf.apply_leave(emp, "2025-11-04")
		c = frappe.db.get_value(CONSUMPTION, {"grant": g.name}, "name")
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(CONSUMPTION, c)
		emp2 = lf.make_employee("2025-04-01").name
		g2 = lf.grant(emp2, "2025-10-01", 10)
		g2.cancel()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(GRANT, g2.name)
		self.assertEqual(getdate(g2.expiry_date), getdate("2027-09-30"))
		from japan_hr_compliance.jp_leave.doctype.jp_annual_leave_grant.jp_annual_leave_grant import retention_until

		self.assertEqual(retention_until(g2), getdate("2032-09-30"))
		frappe.flags.jp_leave_today = getdate("2032-10-01")
		frappe.delete_doc(GRANT, g2.name)
		self.assertFalse(frappe.db.exists(GRANT, g2.name))

	def test_retirement_extinguishes(self):
		"""#24・LVR-34 退職日で器を締め、残日数が「退職による消滅」として記録され、管理簿に出る。"""
		emp = lf.make_employee("2025-04-01")
		g = lf.grant(emp.name, "2025-10-01", 10)
		lf.apply_leave(emp.name, "2025-11-04")
		emp.reload()
		emp.relieving_date = "2026-03-31"
		emp.jp_separation_type = "自己都合"
		emp.save()
		lf.run_daily("2026-03-31")
		self.assertEqual(frappe.db.get_value(GRANT, g.name, "status"), "確定", "退職日当日はまだ消滅しない")
		lf.run_daily("2026-04-01")
		self.assertEqual(frappe.db.get_value(GRANT, g.name, ["status", "remaining_days", "lapsed_days"]), ("消滅", 0, 9))
		self.assertEqual(getdate(frappe.db.get_value("Leave Allocation", g.leave_allocation, "to_date")), getdate("2026-03-31"))
		self.assertEqual(container.hrms_balance(emp.name, LT, "2026-04-01"), 0)
		rows = register.get_data(frappe._dict(company=lf.fx.COMPANY, employee=emp.name, from_date="2024-01-01", to_date="2026-11-15"))
		self.assertEqual((rows[0]["status"], rows[0]["lapsed_days"]), ("消滅", 9))

	def test_absent_treatment_warning(self):
		"""#27・LVR-13 子の看護等休暇を「欠勤」に設定すると警告が表示され、設定者・日時が記録される。"""
		lt = frappe.get_doc("Leave Type", "子の看護等休暇")
		lt.jp_attendance_treatment = "欠勤"
		lt.save()
		self.addCleanup(lambda: frappe.db.set_value("Leave Type", "子の看護等休暇", "jp_attendance_treatment", "出勤とみなす"))
		self.assertTrue(lt.flags.jp_treatment_warning)
		comment = frappe.get_all("Comment", filters={"reference_doctype": "Leave Type", "reference_name": "子の看護等休暇",
			"content": ("like", "%欠勤%")}, fields=["owner", "creation", "content"])
		self.assertTrue(comment)
		self.assertIn("Administrator", comment[0].content)
		self.assertTrue(comment[0].creation)
		maternity = frappe.get_doc("Leave Type", "産前産後休業")
		maternity.jp_attendance_treatment = "欠勤"
		with self.assertRaises(frappe.ValidationError):
			maternity.save()

	def test_nursing_leave_limit_by_children(self):
		"""LVR-72 子の看護等休暇の年度の上限は対象の子1人で5日、2人で10日（B-1 の家族情報から）。"""
		emp = lf.make_employee("2024-04-01")
		emp.reload()
		emp.append("jp_dependents", {"dependent_name": "試験 一郎", "dependent_kana": "シケン イチロウ", "relation": "子",
			"birth_date": "2020-05-01", "gender": "男"})
		emp.save()
		lf.apply_leave(emp.name, "2025-06-02", "2025-06-06", leave_type="子の看護等休暇")  # 5日
		with self.assertRaises(frappe.ValidationError):
			lf.apply_leave(emp.name, "2025-06-09", leave_type="子の看護等休暇")
		emp.reload()
		emp.append("jp_dependents", {"dependent_name": "試験 二郎", "dependent_kana": "シケン ジロウ", "relation": "子",
			"birth_date": "2022-05-01", "gender": "男"})
		emp.save()
		self.assertEqual(leave_rules.annual_limit(emp.name, "子の看護等休暇", "2025-06-09"), (2, 10))
		lf.apply_leave(emp.name, "2025-06-09", leave_type="子の看護等休暇")
		# 年度が替われば（4月1日）上限はリセットされる
		self.assertEqual(leave_rules.leave_year(lf.fx.COMPANY, "2026-03-31"), (getdate("2025-04-01"), getdate("2026-03-31")))
		# 小学校3年生修了（2020-05-01 生まれは 2030-03-31）を過ぎた子は数えない
		self.assertEqual(leave_rules.school_year_end_of_third_grade("2020-05-01"), getdate("2030-03-31"))
		self.assertEqual(leave_rules.school_year_end_of_third_grade("2020-04-01"), getdate("2029-03-31"))

	def test_statutory_leave_periods(self):
		"""LVR-71・73 産前産後休業・育児休業の期間（予定と実績）を記録し、社保免除（SI-008）の判定用に返す。"""
		emp = lf.make_employee("2024-04-01").name
		la = lf.apply_leave(emp, "2025-06-02", "2025-07-13", leave_type="産前産後休業")
		self.assertEqual((str(la.jp_planned_from_date), str(la.jp_planned_to_date)), ("2025-06-02", "2025-07-13"))
		lf.apply_leave(emp, "2025-07-28", "2025-08-29", leave_type="育児休業")
		periods = leave_rules.get_statutory_leave_periods(emp, "2025-06-01", "2025-12-31", si_only=True)
		self.assertEqual([(p.statutory_kind, str(p.from_date)) for p in periods],
			[("産前産後休業", "2025-06-02"), ("育児休業", "2025-07-28")])
		self.assertTrue(all(p.si_exemption for p in periods))

	def test_special_leave_type(self):
		"""LVR-74 会社独自の特別休暇を種別として追加でき、年休の残日数に影響しない。"""
		name = f"テスト特別休暇{frappe.generate_hash(length=4)}"
		frappe.get_doc({"doctype": "Leave Type", "leave_type_name": name, "is_lwp": 1, "jp_leave_category": "特別休暇"}).insert()
		emp = lf.make_employee("2025-04-01").name
		g = lf.grant(emp, "2025-10-01", 10)
		lf.apply_leave(emp, "2025-11-04", leave_type=name)
		self.assertEqual(lf.remaining(g.name), 10)
		self.assertFalse(frappe.db.exists(CONSUMPTION, {"employee": emp}))

	def test_service_start_warnings(self):
		"""LVR-06 起算日に資格取得日を入れたとき・付与済みの従業員の起算日を変更したときは、影響する付与とともに警告する。"""
		emp = lf.make_employee("2025-04-01")
		g = lf.grant(emp.name, "2025-10-01", 10)
		emp.reload()
		emp.jp_hi_acquired = "2025-04-01"
		emp.jp_service_start_date = "2025-04-01"
		emp.save()
		self.assertTrue(any("資格取得日" in w for w in emp.flags.jp_leave_warnings))
		self.assertTrue(any(g.name in w for w in emp.flags.jp_leave_warnings))

	def test_portal_shows_only_own_leave(self):
		"""LVR-63 従業員本人が自分の管理簿（付与・取得・残日数・失効予定・年5日）をポータルで閲覧できる。"""
		from japan_hr_compliance.jp_leave.portal import my_leave
		from japan_hr_compliance.www import jp_leave as page

		user = lf.fx.ensure_user("jp-leave-portal@example.com", ["Employee"])
		emp = lf.make_employee("2025-04-01")
		emp.reload()
		emp.user_id = user
		emp.save()
		other = lf.make_employee("2025-04-01").name
		lf.grant(emp.name, "2025-10-01", 10)
		lf.grant(other, "2025-10-01", 10)
		lf.apply_leave(emp.name, "2025-11-04")
		frappe.flags.jp_leave_today = getdate("2027-08-01")
		data = my_leave(emp.name)
		self.assertEqual((data.balance, len(data.grants), len(data.takes)), (9, 1, 1))
		self.assertEqual(len(data.expiring), 1, "失効予定（3か月以内）")
		frappe.set_user(user)
		ctx = page.get_context(frappe._dict())
		self.assertEqual(ctx.leave.grants[0].name, data.grants[0].name)
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			page.get_context(frappe._dict())
