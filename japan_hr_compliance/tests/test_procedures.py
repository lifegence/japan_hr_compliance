"""入退社・扶養異動の手続きタスクのテスト（B-1 v1.0 EMP-43・80〜82、6章 観点 #12・#14・#15）。"""

import frappe
from frappe.utils import getdate

from japan_hr_compliance.jp_hr_core import procedures
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase


def tasks(employee, event=None, status="未着手"):
	filters = {"employee": employee}
	if event:
		filters["event"] = event
	if status:
		filters["status"] = status
	return {t.rule: t for t in frappe.get_all("JP Procedure Task", filters=filters,
		fields=["name", "rule", "procedure_name", "due_date", "due_text", "base_date", "provisional", "status"])}


class TestProcedureTasks(JPHRTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		procedures.ensure_rules()

	def retire(self, emp, relieving, **kw):
		emp.reload()
		emp.update({"status": "Left", "relieving_date": relieving, "jp_separation_type": "自己都合", **kw})
		emp.save()
		return emp

	def test_due_date_rules(self):
		"""制度ごとの基準日と数え方。"""
		self.assertEqual(procedures.compute_due("2027-04-01", procedures.INCLUSIVE, 5), getdate("2027-04-05"))
		self.assertEqual(procedures.compute_due("2027-04-01", procedures.FROM_NEXT_DAY, 10), getdate("2027-04-11"))
		self.assertEqual(procedures.compute_due("2027-01-31", procedures.NEXT_MONTH_DAY, 10), getdate("2027-02-10"))
		self.assertEqual(procedures.compute_due("2027-12-15", procedures.NEXT_MONTH_DAY, 31), getdate("2028-01-31"))
		self.assertEqual(procedures.compute_due("2027-01-31", procedures.MONTHS, 1), getdate("2027-02-28"))
		self.assertIsNone(procedures.compute_due("2027-01-31", procedures.NO_DUE, 0))

	def test_hire_and_leave_tasks(self):
		"""#12 入社日と退職日を登録すると、2.2 の期限のタスクが作成される（EMP-80）。"""
		emp = fx.make_employee(joining="2026-04-01")
		hired = tasks(emp.name, "入社")
		self.assertEqual(getdate(hired["SI-ACQ"].due_date), getdate("2026-04-05"))  # 資格取得日を含めて5日以内
		self.assertEqual(getdate(hired["EI-ACQ"].due_date), getdate("2026-05-10"))  # 翌月10日
		self.assertEqual(hired["SI-ACQ"].due_text, "資格取得日（入社日）を含めて5日以内")

		emp.reload()
		emp.update({"jp_hi_acquired": "2026-04-01", "jp_pi_acquired": "2026-04-01", "jp_ei_acquired": "2026-04-01"})
		emp.save()
		self.retire(emp, "2026-09-30")
		left = tasks(emp.name, "退職")
		self.assertEqual(set(left), {"SI-LOSS", "EI-LOSS", "WH-SLIP", "RT-CHANGE"})
		self.assertEqual(getdate(left["WH-SLIP"].due_date), getdate("2026-10-30"))  # 退職の日以後1か月以内
		self.assertEqual(getdate(left["RT-CHANGE"].due_date), getdate("2026-10-10"))
		self.assertTrue(left["RT-CHANGE"].provisional)  # 住民税は仮の設定
		self.assertIn("徴収区分は未設定", left["RT-CHANGE"].procedure_name)
		# 同じ契機で保存し直してもタスクは増えない
		emp.reload()
		emp.save()
		self.assertEqual(len(tasks(emp.name, "退職")), 4)

	def test_march_31_retiree_deadlines(self):
		"""#14 3/31 退職者：社保の喪失届 4/5、雇保の喪失届 4/11 が期限（EMP-80）。"""
		emp = fx.make_employee(joining="2020-04-01", jp_hi_acquired="2020-04-01", jp_ei_acquired="2020-04-01")
		self.retire(emp, "2027-03-31")
		left = tasks(emp.name, "退職")
		self.assertEqual(getdate(left["SI-LOSS"].base_date), getdate("2027-04-01"))  # 資格喪失日＝退職日の翌日
		self.assertEqual(getdate(left["SI-LOSS"].due_date), getdate("2027-04-05"))
		self.assertEqual(getdate(left["EI-LOSS"].due_date), getdate("2027-04-11"))
		self.assertEqual(left["SI-LOSS"].due_text, "退職日の翌日（資格喪失日）を含めて5日以内")

		# 退職日を変えると未着手のタスクの期限が更新される（重複しない）
		emp.reload()
		emp.relieving_date = "2027-04-30"
		emp.save()
		left = tasks(emp.name, "退職")
		self.assertEqual(getdate(left["SI-LOSS"].due_date), getdate("2027-05-05"))
		self.assertEqual(getdate(left["EI-LOSS"].due_date), getdate("2027-05-11"))
		self.assertEqual(frappe.db.count("JP Procedure Task", {"employee": emp.name, "rule": "SI-LOSS"}), 1)

	def test_separation_slip_for_59_or_requested(self):
		"""#15 離職日に59歳・交付希望「無」でも離職証明書のタスクが作成される（EMP-81）。"""
		age59 = fx.make_employee(joining="2010-04-01", date_of_birth="1967-03-31", jp_ei_acquired="2010-04-01")
		self.retire(age59, "2026-03-31", jp_separation_slip_requested=0)  # 離職日に59歳ちょうど
		self.assertIn("EI-SLIP", tasks(age59.name, "退職"))
		self.assertEqual(getdate(tasks(age59.name, "退職")["EI-SLIP"].due_date), getdate("2026-04-11"))

		age58 = fx.make_employee(joining="2010-04-01", date_of_birth="1967-04-01", jp_ei_acquired="2010-04-01")
		self.retire(age58, "2026-03-31")  # 離職日は58歳（翌日が誕生日）
		self.assertNotIn("EI-SLIP", tasks(age58.name, "退職"))
		age58.reload()
		age58.jp_separation_slip_requested = 1  # 交付希望ありに変えると作成される
		age58.save()
		self.assertIn("EI-SLIP", tasks(age58.name, "退職"))

		not_insured = fx.make_employee(joining="2010-04-01", date_of_birth="1960-01-01")
		self.retire(not_insured, "2026-03-31")
		self.assertFalse({"EI-LOSS", "EI-SLIP", "SI-LOSS"} & set(tasks(not_insured.name, "退職")))  # 被保険者でない

	def test_resident_tax_classes(self):
		"""退職時の住民税の徴収区分を記録し、区分に応じたタスクを作る（EMP-82）。"""
		emp = fx.make_employee(joining="2020-04-01")
		self.retire(emp, "2026-04-30", jp_resident_tax_on_leave="一括徴収")
		left = tasks(emp.name, "退職")
		self.assertIn("一括徴収", left["RT-CHANGE"].procedure_name)
		self.assertIn("RT-LUMPSUM", left)
		emp.reload()
		emp.jp_resident_tax_on_leave = "普通徴収への切替"
		emp.save()
		left = tasks(emp.name, "退職")
		self.assertIn("普通徴収への切替", left["RT-CHANGE"].procedure_name)
		self.assertNotIn("RT-LUMPSUM", left)
		self.assertEqual(frappe.db.get_value("JP Procedure Task", {"employee": emp.name, "rule": "RT-LUMPSUM"}, "status"), "対象外")
		self.assertEqual(procedures.get_resident_tax_on_leave(emp.name).collection, "普通徴収への切替")

	def test_exempt_reason_controls_hire_tasks(self):
		"""被保険者でない理由に応じて手続きを作らない。兼務役員は雇保の手続きから自動で除外しない（EMP-12）。"""
		officer = fx.make_employee(joining="2026-04-01", jp_si_exempt_reason="役員（雇用保険の対象外）")
		self.assertEqual(set(tasks(officer.name, "入社")), {"SI-ACQ"})
		dual = fx.make_employee(joining="2026-04-01", jp_si_exempt_reason="兼務役員（雇保は労働者部分で判定）")
		self.assertEqual(set(tasks(dual.name, "入社")), {"SI-ACQ", "EI-ACQ"})

	def test_dependent_change_creates_tasks(self):
		"""扶養の異動を異動日付きで記録し、手続きタスクを作る（EMP-43・80）。"""
		emp = fx.make_employee()
		emp.reload()
		emp.jp_dependent_change_date = "2026-11-02"
		emp.append("jp_dependents", {"dependent_name": "試験 一郎", "dependent_kana": "シケン イチロウ", "relation": "子",
			"birth_date": "2026-10-20", "hi_dependent_from": "2026-10-20"})
		emp.save()
		change = frappe.get_all("JP Dependent Change", filters={"employee": emp.name}, fields=["*"])
		self.assertEqual(len(change), 1)
		self.assertEqual((change[0].change_type, change[0].hi_related, change[0].tax_related), ("追加", 1, 1))
		self.assertEqual(getdate(change[0].change_date), getdate("2026-11-02"))
		created = tasks(emp.name, "扶養異動")
		self.assertEqual(getdate(created["DEP-HI"].due_date), getdate("2026-11-06"))
		self.assertIsNone(created["DEP-TAX"].due_date)
		self.assertIsNone(frappe.db.get_value("Employee", emp.name, "jp_dependent_change_date"))

		# 同居の変更は税の扶養だけに関わる → 被扶養者の手続きは作らない
		emp.reload()
		emp.jp_dependents[0].cohabiting = 1
		emp.save()
		latest = frappe.get_all("JP Dependent Change", filters={"employee": emp.name}, fields=["name", "change_type", "hi_related",
			"tax_related", "details"], order_by="creation desc", limit=1)[0]
		self.assertEqual((latest.change_type, latest.hi_related, latest.tax_related), ("区分変更", 0, 1))
		self.assertIn("同居", latest.details)
		self.assertEqual(frappe.db.count("JP Procedure Task", {"employee": emp.name, "rule": "DEP-HI"}), 1)
		self.assertEqual(frappe.db.count("JP Procedure Task", {"employee": emp.name, "rule": "DEP-TAX"}), 2)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("JP Dependent Change", latest.name, ignore_permissions=True)

	def test_completed_task_cannot_be_deleted(self):
		emp = fx.make_employee(joining="2026-04-01")
		task = frappe.get_doc("JP Procedure Task", tasks(emp.name, "入社")["SI-ACQ"].name)
		task.status = "完了"
		task.save()
		self.assertTrue(task.completed_on)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("JP Procedure Task", task.name, ignore_permissions=True)
