"""事業場・業務マスタと通常の労働者の条件のテスト（B-1 v1.0 EMP-29〜31、6章 観点 #17・#18）。"""

import frappe

from japan_hr_compliance.jp_hr_core import workplace as wp
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase


class TestWorkplace(JPHRTestCase):
	def test_dual_labour_insurance_numbers(self):
		"""#18 労働保険番号を2つ持つ事業場（二元適用）を登録し、労災・雇保で別の番号が引ける（EMP-30）。"""
		name = f"テスト建設現場{frappe.generate_hash(length=4)}"
		fx.ensure_workplace(name, units=[
			{"scheme": "労働保険", "labour_scope": "二元適用（労災保険）", "labour_ins_no": "13-3-01-123456-000",
				"valid_from": "2025-04-01"},
			{"scheme": "労働保険", "labour_scope": "二元適用（雇用保険）", "labour_ins_no": "13501654321000", "valid_from": "2025-04-01"},
			{"scheme": "雇用保険", "office_no": "1301-123456-7", "ei_business_class": "建設の事業", "valid_from": "2025-04-01"},
			{"scheme": "健康保険・厚生年金", "parent_workplace": fx.WORKPLACE_HQ, "approval_type": "一括適用承認",
				"office_symbol": "01-テスト", "valid_from": "2025-04-01"},
		])
		self.assertEqual(wp.get_labour_insurance_no(name, "労災保険", "2026-01-01"), "13301123456000")
		self.assertEqual(wp.get_labour_insurance_no(name, "雇用保険", "2026-01-01"), "13501654321000")
		self.assertEqual(wp.get_insurance_unit(name, "雇用保険", "2026-01-01").office_no, "13011234567")
		social = wp.get_insurance_unit(name, "健康保険・厚生年金", "2026-01-01")
		self.assertEqual(social.applied_workplace, fx.WORKPLACE_HQ)
		self.assertIsNone(wp.get_labour_insurance_no(name, "労災保険", "2025-03-31"))

	def test_employee_insurance_unit_follows_contract(self):
		name = f"テスト支店{frappe.generate_hash(length=4)}"
		fx.ensure_workplace(name, units=[{"scheme": "労働保険", "labour_scope": "一元適用", "labour_ins_no": "13101111111000",
			"valid_from": "2025-04-01"}])
		emp = fx.make_employee()
		fx.make_contract(emp.name, "2025-04-01", workplace=name)
		row = wp.get_employee_insurance_unit(emp.name, "労働保険", "2026-01-01", labour_target="雇用保険")
		self.assertEqual(row.labour_ins_no, "13101111111000")

	def test_insurance_unit_validation(self):
		with self.assertRaises(frappe.ValidationError):
			fx.ensure_workplace(f"テスト不正{frappe.generate_hash(length=4)}", units=[
				{"scheme": "労働保険", "labour_scope": "一元適用", "labour_ins_no": "123", "valid_from": "2025-04-01"}])
		with self.assertRaises(frappe.ValidationError):
			fx.ensure_workplace(f"テスト重複{frappe.generate_hash(length=4)}", units=[
				{"scheme": "労働保険", "labour_scope": "一元適用", "labour_ins_no": "13101111111000", "valid_from": "2025-04-01"},
				{"scheme": "労働保険", "labour_scope": "二元適用（労災保険）", "labour_ins_no": "13101111111001", "valid_from": "2026-04-01"}])

	def test_standard_condition_by_job_category(self):
		"""#17 同じ事業場で業務区分の異なるパート2名に、業務区分ごとの通常の労働者の条件が返る（EMP-29）。"""
		def cond(workplace, job, hours, days, valid_from="2025-04-01"):
			frappe.get_doc({"doctype": "JP Standard Worker Condition", "company": fx.COMPANY, "workplace": workplace,
				"job_category": job, "weekly_hours": hours, "monthly_days": days, "valid_from": valid_from}).insert()

		name = f"テスト倉庫{frappe.generate_hash(length=4)}"
		fx.ensure_workplace(name)
		cond(name, None, 40, 21)
		cond(name, fx.JOB_INSPECT, 37.5, 20)
		part = dict(employment_class="パートタイマー", work_rule_class="パートタイマー用", wage_type="時給", weekly_hours_h=20,
			weekly_days=4, monthly_days=16, workplace=name)
		office = fx.make_employee()
		fx.make_contract(office.name, "2025-04-01", job_category=fx.JOB_OFFICE, **part)
		inspector = fx.make_employee()
		fx.make_contract(inspector.name, "2025-04-01", job_category=fx.JOB_INSPECT, **part)
		a = wp.get_employee_standard_condition(office.name, "2026-01-01")
		b = wp.get_employee_standard_condition(inspector.name, "2026-01-01")
		self.assertEqual((a.weekly_hours, a.monthly_days), (40, 21))
		self.assertEqual((b.weekly_hours, b.monthly_days), (37.5, 20))
		# 事業場の行がなければ会社の既定値
		other = f"テスト営業所{frappe.generate_hash(length=4)}"
		fx.ensure_workplace(other)
		if not frappe.db.exists("JP Standard Worker Condition", {"company": fx.COMPANY, "workplace": ("is", "not set"), "valid_from": "2020-01-01"}):
			cond(None, None, 40, 20.5, "2020-01-01")
		self.assertEqual(wp.get_standard_condition(fx.COMPANY, other, fx.JOB_SALES, "2026-01-01").workplace, None)
		with self.assertRaises(frappe.ValidationError):
			cond(None, fx.JOB_SALES, 40, 20)  # 業務区分だけの行は不可

	def test_job_category_fields_are_independent(self):
		"""名簿用の業務名・36協定の文言・雇保職種は別の項目（EMP-31）。"""
		job = frappe.get_doc("JP Job Category", fx.JOB_SALES)
		self.assertEqual((job.job_name, job.agreement36_label, job.ei_job_class), (fx.JOB_SALES, "営業", "04 販売の職業"))
		options = frappe.get_meta("JP Job Category").get_field("ei_job_class").options.split("\n")
		self.assertEqual(len([o for o in options if o]), 11)
