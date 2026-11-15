"""従業員マスタ B-1 の P1 のテスト（EMP-02・04・12・27・33・38・86・91、6章 観点 #3）。"""

import json

import frappe
from frappe.utils import add_days, today

from japan_hr_compliance.jp_hr_core import change_request, consent
from japan_hr_compliance.jp_hr_core.employee import exempt_schemes, get_display_name, notify_residence_expiry
from japan_hr_compliance.jp_hr_core.report.jp_worker_roster.jp_worker_roster import execute as roster, roster_pdf
from japan_hr_compliance.jp_hr_core.workplace import count_regular_workers
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase
from japan_hr_compliance.tests.test_consent import ACCOUNTS
from japan_hr_compliance.tests.test_employee_master import ADDR_OLD

HR = "jp-hr@example.com"
PAYROLL = "jp-payroll@example.com"
LEAVE_TYPE = "テスト年休（B-1）"


class TestEmployeeP1(JPHRTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fx.ensure_user(HR, ["JP HR User"])
		fx.ensure_user(PAYROLL, ["JP Payroll User"])
		if not frappe.db.exists("Leave Type", LEAVE_TYPE):
			frappe.get_doc({"doctype": "Leave Type", "leave_type_name": LEAVE_TYPE, "max_leaves_allowed": 0}).insert(ignore_permissions=True)

	# --- EMP-02 旧姓（通称） ---
	def test_former_name_display(self):
		"""旧姓併記の従業員で、名簿には戸籍上の氏名、明細には通称が出力される（EMP-02）。"""
		emp = fx.make_employee(first_name="花子", jp_former_name="旧姓", jp_display_former_name=1)
		fx.make_contract(emp.name, emp.date_of_joining)
		self.assertEqual(get_display_name(emp.name, "payslip"), "旧姓　花子")
		self.assertEqual(get_display_name(emp.name, "roster"), emp.employee_name)
		data = roster({"company": fx.COMPANY, "employee": emp.name})[1]
		self.assertEqual([r.employee_name for r in data], [emp.employee_name])
		plain = fx.make_employee(first_name="次郎", jp_display_former_name=1)
		self.assertEqual(plain.jp_display_former_name, 0)  # 旧姓がなければ通称の表示はしない
		self.assertEqual(get_display_name(plain.name, "payslip"), plain.employee_name)
		self.assertTrue(frappe.get_meta("Salary Slip").get_field("jp_display_name"))

	# --- EMP-04 在留情報 ---
	def test_residence_info_and_expiry_notice(self):
		"""在留カード番号の形式と、在留期間の満了日の60日前の通知（EMP-04）。"""
		with self.assertRaises(frappe.ValidationError):
			fx.make_employee(jp_residence_card_no="A1234567890B")
		emp = fx.make_employee(jp_nationality="ベトナム", jp_residence_status="技術・人文知識・国際業務",
			jp_residence_card_no="ab 12345678 cd", jp_romaji_name="nguyen  van a", jp_residence_expiry=add_days(today(), 60))
		self.assertEqual((emp.jp_residence_card_no, emp.jp_romaji_name), ("AB12345678CD", "NGUYEN VAN A"))
		self.assertEqual(frappe.get_meta("Employee").get_field("jp_residence_card_no").permlevel, 2)
		later = fx.make_employee(jp_residence_expiry=add_days(today(), 61))
		notified = notify_residence_expiry()
		self.assertIn(emp.name, notified)
		self.assertNotIn(later.name, notified)
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": HR, "document_name": emp.name, "document_type": "Employee"}))

	# --- EMP-12 被保険者でない場合の理由 ---
	def test_si_exempt_reason(self):
		"""区分ごとに対象外の制度が決まり、兼務役員は雇保の判定から自動で除外されない（EMP-12）。"""
		self.assertEqual(exempt_schemes("兼務役員（雇保は労働者部分で判定）"), set())
		self.assertEqual(exempt_schemes("役員（雇用保険の対象外）"), {"雇用保険"})
		self.assertEqual(exempt_schemes("70歳以上（厚生年金の資格喪失）"), {"厚生年金"})
		self.assertEqual(exempt_schemes("75歳以上（後期高齢者医療）"), {"健康保険"})
		self.assertEqual(exempt_schemes(None), set())
		field = frappe.get_meta("Employee").get_field("jp_si_exempt_reason")
		self.assertEqual(field.permlevel, 2)
		self.assertIn("兼務役員（雇保は労働者部分で判定）", field.options.split("\n"))

	# --- EMP-27 遡及変更 ---
	def test_retroactive_change_warns_with_impacts(self):
		"""#3 年休の基準日をまたいで契約を遡及変更すると、影響する付与の一覧と警告（EMP-27）。"""
		emp = fx.make_employee(joining="2025-04-01")
		fx.make_contract(emp.name, "2025-04-01")
		alloc = frappe.get_doc({"doctype": "Leave Allocation", "employee": emp.name, "leave_type": LEAVE_TYPE,
			"from_date": "2025-10-01", "to_date": "2026-09-30", "new_leaves_allocated": 10})
		alloc.insert(ignore_permissions=True)
		alloc.submit()
		frappe.clear_messages()
		change = fx.make_contract(emp.name, "2025-09-01", submit=False, change_reason="条件変更", weekly_days=3, weekly_hours_h=24)
		self.assertIn(alloc.name, change.retro_impact)
		self.assertIn("年休の付与", change.retro_impact)
		self.assertTrue(any("遡及変更" in (m.get("title") or "") for m in frappe.get_message_log()))
		change.submit()

		# 基準日より後に始まる変更・将来の変更は影響なし
		later = fx.make_contract(emp.name, "2025-11-01", submit=False, change_reason="条件変更")
		self.assertFalse(later.retro_impact)
		future = fx.make_contract(emp.name, add_days(today(), 30), submit=False, change_reason="条件変更")
		self.assertFalse(future.retro_impact)
		# 2件目以降は変更理由が必須
		with self.assertRaises(frappe.ValidationError):
			fx.make_contract(emp.name, "2026-01-01", submit=False, change_reason=None)

	# --- EMP-33 常時使用労働者数 ---
	def test_regular_worker_count(self):
		"""指定日時点の人数を事業場別に数える。パート・休職者を含み、日雇い・退職者は含めない（EMP-33）。"""
		wp = f"テスト人数事業場{frappe.generate_hash(length=4)}"
		fx.ensure_workplace(wp)
		regular = fx.make_employee(joining="2025-04-01")
		fx.make_contract(regular.name, "2025-04-01", workplace=wp)
		part = fx.make_employee(joining="2025-04-01")
		fx.make_contract(part.name, "2025-04-01", workplace=wp, employment_class="パートタイマー", work_rule_class="パートタイマー用")
		on_leave = fx.make_employee(joining="2025-04-01", status="Inactive")  # 休職（無給を含む）
		fx.make_contract(on_leave.name, "2025-04-01", workplace=wp)
		daily = fx.make_employee(joining="2025-04-01")
		fx.make_contract(daily.name, "2025-04-01", workplace=wp, employment_class="日雇い")
		gone = fx.make_employee(joining="2025-04-01")
		fx.make_contract(gone.name, "2025-04-01", workplace=wp)
		gone.reload()
		gone.update({"status": "Left", "relieving_date": "2026-03-31", "jp_separation_type": "自己都合"})
		gone.save()
		self.assertEqual(count_regular_workers("2026-11-01", fx.COMPANY, wp), {wp: 3})
		self.assertEqual(count_regular_workers("2026-03-31", fx.COMPANY, wp), {wp: 4})
		# 日雇いは名簿の対象外
		names = [r.employee for r in roster({"company": fx.COMPANY, "workplace": wp})[1]]
		self.assertNotIn(daily.name, names)
		self.assertIn(part.name, names)

	# --- EMP-38 書面の同意の代理登録 ---
	def test_paper_consent_registration(self):
		"""ポータルを使わない従業員の同意を代理登録でき、方法が「書面」と記録される（EMP-38）。"""
		emp = fx.make_employee(jp_bank_accounts=ACCOUNTS)
		frappe.set_user(PAYROLL)
		with self.assertRaises(frappe.ValidationError):
			consent.register_paper_consent(emp.name, consent.PAYSLIP, attachment=None)
		name = consent.register_paper_consent(emp.name, consent.BANK_TRANSFER, attachment="/private/files/paper-consent.pdf",
			wage_scope="月例給与の全額", start_period="2027年1月支給分から")
		frappe.set_user(HR)
		with self.assertRaises(frappe.PermissionError):
			consent.register_paper_consent(emp.name, consent.PAYSLIP, attachment="/private/files/x.pdf")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("JP Employee Consent", name)
		self.assertEqual((doc.method, doc.user, doc.attachment), ("書面", PAYROLL, "/private/files/paper-consent.pdf"))
		self.assertTrue(consent.has_consent(emp.name, consent.BANK_TRANSFER))

	# --- EMP-86 ポータルの変更申請 ---
	def test_portal_change_requests(self):
		"""本人の住所・口座・扶養の変更を申請でき、承認前はマスタに反映されない（EMP-86）。"""
		email = fx.ensure_user(f"jp-cr-{frappe.generate_hash(length=6)}@example.com", ["Employee"])
		emp = fx.make_employee(user_id=email, jp_addresses=[ADDR_OLD], jp_bank_accounts=ACCOUNTS)
		frappe.get_doc("User", email).add_roles("Employee")
		frappe.set_user(email)
		addr = change_request.submit_change_request("住所", json.dumps({"valid_from": "2026-12-01", "postal_code": "1700013",
			"prefecture": "東京都", "municipality": "豊島区", "lg_code": "131164", "address_line": "東池袋（架空）"}))
		bank = change_request.submit_change_request("振込口座", {"accounts": [dict(ACCOUNTS[1], priority=1)]})
		dep = change_request.submit_change_request("扶養親族", {"action": "追加", "change_date": "2026-11-20",
			"values": {"dependent_name": "試験 次郎", "dependent_kana": "しけん じろう", "relation": "子", "birth_date": "2026-11-10",
				"hi_dependent_from": "2026-11-10"}})
		with self.assertRaises(frappe.PermissionError):
			change_request.approve(addr)  # 本人は承認できない
		frappe.set_user("Administrator")
		emp.reload()
		self.assertEqual(len(emp.jp_addresses), 1)  # 承認前は反映されない
		self.assertEqual(len(emp.jp_bank_accounts), 2)
		self.assertEqual(len(emp.jp_dependents), 0)
		with self.assertRaises(frappe.PermissionError):
			req = frappe.get_doc("JP Employee Change Request", addr)
			req.summary = "改ざん"
			req.save(ignore_permissions=True)

		frappe.set_user(HR)
		change_request.approve(addr)
		change_request.approve(dep)
		frappe.set_user(PAYROLL)
		with self.assertRaises(frappe.PermissionError):
			change_request.approve(dep)  # 処理済み以前に、扶養は給与担当の承認対象外
		change_request.approve(bank)
		frappe.set_user("Administrator")
		emp.reload()
		self.assertEqual([str(a.valid_from) for a in emp.jp_addresses], ["2025-04-01", "2026-12-01"])
		self.assertEqual([a.bank_code for a in emp.jp_bank_accounts], ["9900"])
		self.assertEqual(emp.jp_dependents[0].dependent_kana, "シケン　ジロウ")
		change = frappe.get_doc("JP Dependent Change", {"employee": emp.name})
		self.assertEqual((change.change_request, str(change.change_date)), (dep, "2026-11-20"))
		self.assertEqual(frappe.db.get_value("JP Employee Change Request", addr, ["status", "decided_by"]), ("承認", HR))

		frappe.set_user(email)
		rejected = change_request.submit_change_request("住所", {"valid_from": "2027-01-01", "municipality": "新宿区"})
		frappe.set_user(HR)
		with self.assertRaises(frappe.ValidationError):
			change_request.reject(rejected, "")
		change_request.reject(rejected, "転居日の確認が必要です")
		with self.assertRaises(frappe.ValidationError):
			change_request.approve(rejected)
		frappe.set_user("Administrator")
		emp.reload()
		self.assertEqual(len(emp.jp_addresses), 2)

	# --- EMP-91 電磁的保存 ---
	def test_electronic_records(self):
		"""指定した従業員・期間の名簿を即時に PDF 出力でき、確定済みの記録・変更履歴は削除できない（EMP-91）。"""
		emp = fx.make_employee(jp_addresses=[ADDR_OLD])
		contract = fx.make_contract(emp.name, emp.date_of_joining)
		pdf = roster_pdf({"company": fx.COMPANY, "employee": emp.name, "from_date": "2025-04-01", "to_date": "2026-03-31"})
		self.assertEqual(pdf[:4], b"%PDF")
		contract.cancel()
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("JP Employment Contract", contract.name)
		emp.reload()
		emp.jp_first_name_kana = "ジロウ"
		emp.save(ignore_version=False)  # テストでは既定で Version を作らないため明示する
		version = frappe.get_all("Version", filters={"ref_doctype": "Employee", "docname": emp.name}, pluck="name", limit=1)
		self.assertTrue(version)
		# 変更履歴の削除は japan_compliance（AUD-11）が全 DocType について止めている
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			frappe.delete_doc("Version", version[0], ignore_permissions=True)
