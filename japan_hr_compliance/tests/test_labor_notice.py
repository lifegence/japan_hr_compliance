"""労働条件通知書のテスト（B-1 v1.0 EMP-50〜52）。"""

import frappe

from japan_hr_compliance.jp_hr_core import consent, labor_notice
from japan_hr_compliance.jp_hr_core.doctype.jp_labor_condition_notice.jp_labor_condition_notice import make_from_contract
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase

MANUAL_ITEMS = {
	"work_time_text": "始業 9時00分　終業 15時00分", "overtime_text": "無", "break_text": "60分", "holiday_text": "土日・祝日",
	"leave_text": "年次有給休暇（労基法39条）", "shift_text": "なし", "wage_text": "時給 1,300円（架空）。毎月末締め・翌月25日払い・口座振込",
	"wage_payment_text": "毎月末日締め、翌月25日支払", "raise_text": "なし", "retirement_text": "就業規則第○条（架空）による",
}
PART_ITEMS = {"pt_raise": "無", "pt_retirement_allowance": "無", "pt_bonus": "有", "consultation_desk": "人事部 相談窓口（架空）"}
SCOPES = {"change_scope_place": "会社の定める事業場", "change_scope_job": "会社の定める業務"}


class TestLaborConditionNotice(JPHRTestCase):
	def part_timer(self):
		emp = fx.make_employee(joining="2026-04-01")
		contract = fx.make_contract(emp.name, "2026-04-01", employment_class="パートタイマー", work_rule_class="パートタイマー用",
			wage_type="時給", fixed_term=1, contract_end="2027-03-31", renewal="更新する場合がある", renewal_criteria="業務量・勤務成績",
			renewal_limit_count=4, expected_period="2か月を超える見込み", weekly_hours_h=24, weekly_days=4, daily_hours_h=6,
			monthly_days=17, **SCOPES)
		return emp, contract

	def test_completeness_check_and_pdf(self):
		"""明示事項の一覧との突合で漏れがなく、未入力の明示事項があると発行できない。交付した版を PDF で保存（EMP-50・51）。"""
		emp, contract = self.part_timer()
		doc = frappe.get_doc("JP Labor Condition Notice", make_from_contract(contract.name))
		self.assertEqual(doc.notice_type, "雇入れ時")
		self.assertTrue(doc.part_or_fixed)
		self.assertEqual(doc.renewal_limit_text, "更新回数 4 回まで")
		self.assertIn("期間の定めあり（2026-04-01〜2027-03-31）", doc.contract_period_text)
		self.assertIn("テスト本社事業場", doc.place_text)
		self.assertEqual(doc.change_scope_job, "会社の定める業務")
		self.assertIn("始業・終業の時刻", doc.missing_items)
		with self.assertRaises(frappe.ValidationError):
			doc.submit()
		doc.reload()
		doc.update({**MANUAL_ITEMS, "pt_raise": "無", "pt_retirement_allowance": "無", "pt_bonus": "有"})
		doc.save()
		self.assertEqual(doc.missing_items, "相談窓口")
		doc.consultation_desk = PART_ITEMS["consultation_desk"]
		doc.save()
		self.assertFalse(doc.missing_items)
		doc.submit()
		self.assertTrue(doc.pdf)
		file = frappe.get_doc("File", {"file_url": doc.pdf, "attached_to_name": doc.name})
		self.assertTrue(file.get_content()[:4] == b"%PDF")

		# 一覧の項目（2024年4月の追加事項を含む）がすべて雛形に出る
		labels = [r.label for r in labor_notice.items_for_print(doc)]
		for item in ("更新上限（通算契約期間または更新回数の上限）", "就業の場所の変更の範囲", "業務の変更の範囲", "相談窓口",
				"待遇の相違の内容・理由等の説明を求めることができる旨"):
			self.assertIn(item, labels)
		html = frappe.render_template("japan_hr_compliance/templates/jp_labor_condition_notice.html", {"doc": doc})
		self.assertIn("会社の定める業務", html)

		# 交付した通知書は削除できない（取消後も）
		doc.cancel()
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("JP Labor Condition Notice", doc.name)

	def test_conversion_and_renewal_limit_required(self):
		"""無期転換の申込機会と転換後の条件（2024年4月追加）・更新上限の明示（EMP-50）。"""
		emp = fx.make_employee(joining="2026-04-01")
		contract = fx.make_contract(emp.name, "2026-04-01", employment_class="契約社員", fixed_term=1, contract_end="2027-03-31",
			renewal="更新する場合がある", renewal_criteria="勤務成績", **SCOPES)
		doc = frappe.get_doc({"doctype": "JP Labor Condition Notice", "employee": emp.name, "contract": contract.name,
			"notice_type": "契約更新時", "issue_date": "2026-11-15", "conversion_right": 1, **MANUAL_ITEMS, **PART_ITEMS})
		doc.insert()
		for item in ("更新上限", "無期転換の申込機会", "無期転換後の労働条件"):
			self.assertIn(item, doc.missing_items)
		doc.update({"renewal_limit_text": "なし", "conversion_opportunity_text": "本契約期間中に申込みができます",
			"conversion_conditions_text": "契約期間以外は現在と同じ"})
		doc.save()
		self.assertFalse(doc.missing_items)
		self.assertEqual(doc.renewal_limit_text, "なし")  # 手入力の「なし」は契約からの差し込みで消えない

	def test_explanation_right_on_hire_and_renewal(self):
		"""2026年10月追加の「待遇の相違の内容・理由等の説明を求めることができる旨」が雇入れ時・更新時の両方に入る（EMP-52）。"""
		emp, contract = self.part_timer()
		for notice_type in ("雇入れ時", "契約更新時"):
			doc = frappe.get_doc({"doctype": "JP Labor Condition Notice", "employee": emp.name, "contract": contract.name,
				"notice_type": notice_type, **MANUAL_ITEMS, **PART_ITEMS}).insert()
			self.assertEqual(doc.explanation_right_text, labor_notice.EXPLANATION_RIGHT_TEXT)
			html = frappe.render_template("japan_hr_compliance/templates/jp_labor_condition_notice.html", {"doc": doc})
			self.assertIn("説明を求めることができます", html)
		regular = fx.make_employee(joining="2026-04-01")
		rc = fx.make_contract(regular.name, "2026-04-01", **SCOPES)
		doc = frappe.get_doc({"doctype": "JP Labor Condition Notice", "employee": regular.name, "contract": rc.name,
			"notice_type": "雇入れ時", **MANUAL_ITEMS}).insert()
		self.assertFalse(doc.part_or_fixed)
		self.assertFalse(doc.missing_items)  # 正社員（無期）は更新・パート有期の事項が不要
		self.assertNotIn("待遇の相違", str([r.label for r in labor_notice.items_for_print(doc)]))

	def test_missing_change_scope_blocks_issue(self):
		"""契約に変更の範囲がないと明示事項の漏れになる。"""
		emp = fx.make_employee(joining="2026-04-01")
		contract = fx.make_contract(emp.name, "2026-04-01")
		doc = frappe.get_doc({"doctype": "JP Labor Condition Notice", "employee": emp.name, "contract": contract.name,
			"notice_type": "雇入れ時", **MANUAL_ITEMS}).insert()
		self.assertEqual(doc.missing_items.split("\n"), ["就業の場所の変更の範囲", "業務の変更の範囲"])

	def test_electronic_delivery_requires_request(self):
		"""電子交付で希望の記録がないと交付済にできない（EMP-51）。"""
		emp, contract = self.part_timer()
		doc = frappe.get_doc({"doctype": "JP Labor Condition Notice", "employee": emp.name, "contract": contract.name,
			"notice_type": "雇入れ時", "method": "電子メール等", **MANUAL_ITEMS, **PART_ITEMS}).insert()
		with self.assertRaises(frappe.ValidationError):
			doc.submit()
		consent.record_consent(emp.name, consent.LABOR_NOTICE)
		doc.reload()
		doc.save()
		self.assertTrue(doc.electronic_request)
		self.assertTrue(doc.consent_record)
		doc.submit()
		self.assertEqual(doc.docstatus, 1)
