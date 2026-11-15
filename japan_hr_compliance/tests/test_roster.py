"""労働者名簿（JP）のテスト（B-1 v1.0 EMP-44〜46、6章 観点 #8・#9）。"""

import frappe

from japan_hr_compliance.jp_hr_core.report.jp_worker_roster.jp_worker_roster import execute
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase
from japan_hr_compliance.tests.test_employee_master import ADDR_OLD


class TestWorkerRoster(JPHRTestCase):
	def run_report(self, **filters):
		columns, data, message = execute({"company": fx.COMPANY, **filters})[:3]
		return columns, {r.employee: r for r in data}, message

	def test_roster_by_workplace_with_retired(self):
		"""#9 在籍者・退職者を含む名簿を事業場別に出力。記載事項①〜⑨が出て、空欄は警告（EMP-45）。"""
		wp_name = f"テスト名簿事業場{frappe.generate_hash(length=4)}"
		fx.ensure_workplace(wp_name)
		active = fx.make_employee(jp_addresses=[ADDR_OLD])
		fx.make_contract(active.name, "2025-04-01", workplace=wp_name)
		retired = fx.make_employee(jp_addresses=[ADDR_OLD])
		fx.make_contract(retired.name, "2025-04-01", workplace=wp_name)
		retired.reload()
		retired.update({"status": "Left", "relieving_date": "2026-03-31", "jp_separation_type": "解雇",
			"jp_separation_reason": "勤務成績不良（架空）", "jp_dismissal_reason": "就業規則第○条（架空）"})
		retired.save()
		dead = fx.make_employee()  # 住所なし → 警告
		fx.make_contract(dead.name, "2025-04-01", workplace=wp_name)
		dead.reload()
		dead.update({"status": "Left", "relieving_date": "2026-05-10", "jp_separation_type": "死亡",
			"jp_death_date": "2026-05-10", "jp_death_cause": "病気（架空）"})
		dead.save()
		other = fx.make_employee(jp_addresses=[ADDR_OLD])
		fx.make_contract(other.name, "2025-04-01", workplace=fx.WORKPLACE_FACTORY)

		columns, rows, message = self.run_report(workplace=wp_name)
		labels = [c["label"] for c in columns]
		for item in ("① 氏名", "② 生年月日", "③ 履歴", "④ 性別", "⑤ 住所", "⑥ 従事する業務の種類", "⑦ 雇入れの年月日",
				"⑧ 退職の年月日", "⑧ 退職の事由", "⑨ 死亡の年月日", "⑨ 死亡の原因"):
			self.assertIn(item, labels)
		self.assertEqual(set(rows), {active.name, retired.name, dead.name})
		r = rows[active.name]
		self.assertEqual((r.name_kana, r.gender, r.job_name), ("シケン　タロウ", "男", fx.JOB_OFFICE))
		self.assertIn("豊島区", r.address)
		self.assertIn("就業規則第○条（架空）", rows[retired.name].separation)
		self.assertEqual(str(rows[dead.name].death_date), "2026-05-10")
		self.assertIn(dead.name, message)
		self.assertIn("⑤ 住所", message)
		self.assertNotIn(active.name, message)

		_, rows, _ = self.run_report(workplace=wp_name, status="在籍者")
		self.assertEqual(set(rows), {active.name})
		_, rows, _ = self.run_report(workplace=wp_name, status="退職者", from_date="2026-04-01")
		self.assertEqual(set(rows), {dead.name})

	def test_history_lists_changes(self):
		"""異動2回・区分転換1回の従業員で3件の履歴が出力される（EMP-46）。"""
		emp = fx.make_employee(joining="2024-04-01")
		fx.make_contract(emp.name, "2024-04-01")
		fx.make_contract(emp.name, "2025-04-01", workplace=fx.WORKPLACE_FACTORY, change_reason="異動")
		fx.make_contract(emp.name, "2025-10-01", workplace=fx.WORKPLACE_FACTORY, job_category=fx.JOB_INSPECT, change_reason="異動")
		fx.make_contract(emp.name, "2026-04-01", workplace=fx.WORKPLACE_FACTORY, job_category=fx.JOB_INSPECT,
			employment_class="嘱託", change_reason="雇用区分の転換")
		_, rows, _ = self.run_report()
		history = rows[emp.name].history.split("\n")
		self.assertEqual(len(history), 3)
		self.assertTrue(history[0].startswith("2025-04-01 事業場"))
		self.assertIn("雇用区分 正社員→嘱託", history[2])

	def test_external_history_is_optional(self):
		emp = fx.make_employee(external_work_history=[{"company_name": "テスト前職株式会社", "designation": "営業"}])
		fx.make_contract(emp.name, "2025-04-01")
		_, rows, _ = self.run_report()
		self.assertNotIn("テスト前職株式会社", rows[emp.name].history)
		frappe.db.set_single_value("JP HR Settings", "roster_include_external_history", 1)
		try:
			_, rows, _ = self.run_report()
			self.assertIn("入社前：テスト前職株式会社", rows[emp.name].history)
		finally:
			frappe.db.set_single_value("JP HR Settings", "roster_include_external_history", 0)
