"""マイナンバーのテスト（B-1 v1.0 EMP-60〜67、6章 観点 #10・#11・#16）。番号はすべて架空（検査用数字だけ合わせたもの）。"""

import frappe
from frappe.utils import getdate

from japan_hr_compliance.jp_hr_core import my_number as mn
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase

OFFICER = "jp-mn-officer@example.com"
HR = "jp-hr@example.com"
PAYROLL = "jp-payroll@example.com"
SYSMAN = "jp-sysman@example.com"


def fake_number(prefix11):
	"""架空の番号（先頭11桁＋検査用数字）。"""
	return prefix11 + str(mn.check_digit(prefix11))


def wrong_check_digit(prefix11):
	return prefix11 + str((mn.check_digit(prefix11) + 1) % 10)


class TestMyNumber(JPHRTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fx.ensure_user(OFFICER, ["JP My Number Officer"])
		fx.ensure_user(HR, ["JP HR User"])
		fx.ensure_user(PAYROLL, ["JP Payroll User"])
		fx.ensure_user(SYSMAN, ["System Manager"])
		mn.ensure_retention_rules()

	def register(self, employee, number, **kw):
		frappe.set_user(OFFICER)
		doc = frappe.get_doc({"doctype": "JP My Number", "employee": employee, "owner_type": "本人", "my_number": number, **kw})
		doc.insert()
		frappe.set_user("Administrator")
		return doc

	def reveal(self, user, name, reason_type="届出書の作成（社会保険）", reason="資格取得届の作成（テスト）"):
		frappe.set_user(user)
		try:
			return mn.reveal(name, reason_type, reason)
		finally:
			frappe.set_user("Administrator")

	def test_check_digit(self):
		"""検査用数字の算式（平成26年総務省令第85号第5条）。手計算した例と一致する。"""
		# 最下位から P1=1,P2=0,P3=9,…,P11=1。1×2+0×3+9×4+8×5+7×6+6×7+5×2+4×3+3×4+2×5+1×6 = 212、
		# 212 mod 11 = 3 → 11−3 = 8
		self.assertEqual(mn.check_digit("12345678901"), 8)
		self.assertEqual(mn.normalize_number("１２３４ ５６７８ ９０１８"), "123456789018")
		self.assertEqual(mn.check_digit("00000000000"), 0)  # 余り0 → 0
		for bad in ("12345678901", "1234567890123", "12345678901a"):
			with self.assertRaises(ValueError):
				mn.normalize_number(bad)

	def test_wrong_check_digit_rejected(self):
		"""#11 検査用数字の誤った番号を登録すると保存時エラー（EMP-60）。エラー文言に番号を含めない。"""
		emp = fx.make_employee()
		bad = wrong_check_digit("98765432109")
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.register(emp.name, bad)
		self.assertNotIn(bad, str(ctx.exception))
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("JP My Number", {"employee": emp.name}))

	def test_encrypted_storage_and_no_plaintext(self):
		"""DB（DocType の列・__Auth・Version・ログ）に平文が残らない（EMP-60・67）。"""
		emp = fx.make_employee()
		number = fake_number("31415926535")
		doc = self.register(emp.name, number, number_check_doc="個人番号カード")
		self.assertEqual(frappe.db.get_value("JP My Number", doc.name, "my_number"), "*" * 12)
		frappe.set_user(OFFICER)
		doc.reload()
		doc.my_number = fake_number("27182818284")  # 変更
		doc.identity_check_doc = "運転免許証"  # 他の項目も変えて変更履歴（Version）を作らせる
		doc.save(ignore_version=False)  # テストでは既定で Version を作らないため明示する
		frappe.set_user("Administrator")
		old_and_new = (number, fake_number("27182818284"))
		dumps = [
			str(frappe.db.sql("select * from `tabJP My Number` where name=%s", doc.name)),
			str(frappe.db.sql("select * from `__Auth` where doctype='JP My Number' and name=%s", doc.name)),
			str(frappe.db.sql("select data from `tabVersion` where ref_doctype='JP My Number' and docname=%s", doc.name)),
			str(frappe.db.sql("select * from `tabJP My Number Access Log` where my_number=%s", doc.name)),
			str(frappe.db.sql("select * from `tabComment` where reference_doctype='JP My Number' and reference_name=%s", doc.name)),
		]
		for value in old_and_new:
			for dump in dumps:
				self.assertNotIn(value, dump)
				self.assertNotIn(value[-4:] + "'", dump)
		self.assertTrue(frappe.db.sql("select 1 from `__Auth` where doctype='JP My Number' and name=%s", doc.name))
		self.assertEqual(self.reveal(OFFICER, doc.name), old_and_new[1])
		versions = frappe.get_all("Version", filters={"ref_doctype": "JP My Number", "docname": doc.name}, pluck="data")
		self.assertTrue(versions)  # 変更履歴は残るが番号は含まない（番号の項目は前後とも「*」で差分にならない）
		self.assertTrue(frappe.db.exists("JP My Number Access Log", {"my_number": doc.name, "action": "変更"}))

	def test_save_reveal_and_roles(self):
		"""#10 保存を3回繰り返し、各ロールで表示・復号・エクスポートを試行（EMP-62〜65）。"""
		emp = fx.make_employee()
		number = fake_number("11122233344")
		doc = self.register(emp.name, number)
		for i in range(3):
			frappe.set_user(OFFICER)
			doc.reload()
			doc.purpose_notified_on = f"2026-11-{10 + i}"
			doc.save()
			frappe.set_user("Administrator")
			self.assertEqual(self.reveal(OFFICER, doc.name), number)  # 復号値は不変
		doc.reload()
		self.assertEqual(doc.masked_number, mn.MASK)

		# 事務取扱担当者以外（System Manager・Administrator を含む）は復号できない
		for user in (HR, PAYROLL, SYSMAN, "Administrator"):
			with self.assertRaises(frappe.PermissionError, msg=user):
				self.reveal(user, doc.name)
		# 理由の入力が必須
		for reason_type, reason in (("", "届出"), ("その他", " ")):
			with self.assertRaises(frappe.ValidationError):
				self.reveal(OFFICER, doc.name, reason_type, reason)
		# 標準の frappe.client.get_password（System Manager 向け）でも取得できない
		self.assertEqual(frappe.get_hooks("override_whitelisted_methods")["frappe.client.get_password"][-1],
			"japan_hr_compliance.jp_hr_core.my_number.guarded_get_password")
		frappe.set_user(SYSMAN)
		with self.assertRaises(frappe.PermissionError):
			mn.guarded_get_password("JP My Number", doc.name, "my_number")
		frappe.set_user("Administrator")

		# エクスポート・レポート・印刷の権限はどのロールにもない。一覧・API の値は「*」だけ
		for user in (OFFICER, HR, PAYROLL, SYSMAN):
			for ptype in ("export", "report", "print"):
				self.assertFalse(frappe.has_permission("JP My Number", ptype, user=user), f"{user} {ptype}")
			frappe.set_user(user)
			rows = frappe.get_list("JP My Number", filters={"name": doc.name}, fields=["*"])
			frappe.set_user("Administrator")
			self.assertNotIn(number, str(rows))
			self.assertNotIn(number[-4:], str([r.get("my_number") for r in rows]))
		self.assertTrue(frappe.get_meta("JP My Number").get_field("my_number").print_hide)
		self.assertTrue(frappe.get_meta("JP My Number").get_field("my_number").report_hide)

		# すべての操作がログに残る：登録1・復号（3回＋最後の確認で4回）
		logs = frappe.get_all("JP My Number Access Log", filters={"my_number": doc.name}, fields=["action", "user", "reason"])
		self.assertEqual(sum(1 for x in logs if x.action == "登録"), 1)
		self.assertEqual(sum(1 for x in logs if x.action == "復号"), 3)
		self.assertTrue(all(x.user == OFFICER for x in logs))
		self.reveal(OFFICER, doc.name)
		self.assertEqual(frappe.db.count("JP My Number Access Log", {"my_number": doc.name, "action": "復号"}), 4)

	def test_general_role_sees_full_mask(self):
		"""#16 一般ロールでマイナンバーのある従業員を表示すると、番号は全桁マスクで下4桁も出ない（EMP-62）。"""
		email = fx.ensure_user(f"jp-mn-self-{frappe.generate_hash(length=6)}@example.com", ["Employee"])
		emp = fx.make_employee(user_id=email)
		frappe.get_doc("User", email).add_roles("Employee")
		number = fake_number("55566677788")
		doc = self.register(emp.name, number)
		for user in (email, HR):
			frappe.set_user(user)
			loaded = frappe.get_doc("JP My Number", doc.name)
			loaded.apply_fieldlevel_read_permissions()
			shown = str(loaded.as_dict())
			emp_shown = frappe.get_doc("Employee", emp.name).as_dict()
			frappe.set_user("Administrator")
			self.assertEqual(loaded.masked_number, "＊＊＊＊＊＊＊＊＊＊＊＊")
			self.assertIsNone(loaded.get("my_number"))  # 権限レベル1の入力欄は見えない
			self.assertNotIn(number[-4:], shown)
			self.assertNotIn(number, str(emp_shown))  # Employee・扶養の表は番号を持たない
		self.assertFalse(frappe.get_meta("Employee").get_field("my_number"))

	def test_registration_only_by_officer_and_verification(self):
		"""登録は事務取扱担当者のみ。本人確認の記録がない番号は「未確認」（EMP-61）。"""
		emp = fx.make_employee()
		frappe.set_user(HR)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({"doctype": "JP My Number", "employee": emp.name, "my_number": fake_number("10203040506")}).insert(
				ignore_permissions=True)
		frappe.set_user("Administrator")
		doc = self.register(emp.name, fake_number("10203040506"))
		self.assertEqual(doc.verification_status, "未確認")
		frappe.set_user(OFFICER)
		doc.update({"number_check_doc": "個人番号カード", "identity_check_doc": "個人番号カード", "verified_on": "2026-11-15",
			"verified_by": OFFICER, "purpose_notified_on": "2026-11-15"})
		doc.save()
		frappe.set_user("Administrator")
		self.assertEqual(doc.verification_status, "確認済")
		with self.assertRaises(frappe.ValidationError):
			self.register(emp.name, fake_number("60504030201"))  # 本人の有効な番号は1件

	def test_access_log_is_append_only(self):
		"""ログは追記のみで、編集・削除はすべてのロールで不可（EMP-64）。"""
		emp = fx.make_employee()
		doc = self.register(emp.name, fake_number("12121212121"))
		log = frappe.get_doc("JP My Number Access Log", {"my_number": doc.name})
		log.reason = "改ざん"
		with self.assertRaises(frappe.PermissionError):
			log.save(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("JP My Number Access Log", log.name, ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({"doctype": "JP My Number Access Log", "action": "復号", "my_number": doc.name}).insert(ignore_permissions=True)
		for user in (OFFICER, SYSMAN):
			for ptype in ("write", "delete", "create"):
				self.assertFalse(frappe.has_permission("JP My Number Access Log", ptype, user=user))
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("JP My Number", doc.name, ignore_permissions=True)  # 番号の記録も削除できない（廃棄を使う）

	def test_output_for_filing_requires_officer_and_logs(self):
		"""届出データ等への出力は事務取扱担当者の操作と理由入力が条件で、「出力」のログが残る（EMP-65）。"""
		emp = fx.make_employee()
		number = fake_number("13579135791")
		doc = self.register(emp.name, number)
		frappe.set_user(HR)
		with self.assertRaises(frappe.PermissionError):
			mn.get_numbers_for_output([doc.name], "届出書の作成（雇用保険）", "資格取得届", "雇用保険 資格取得届")
		frappe.set_user(OFFICER)
		result = mn.get_numbers_for_output([doc.name], "届出書の作成（雇用保険）", "資格取得届の作成", "雇用保険 資格取得届")
		frappe.set_user("Administrator")
		self.assertEqual(result, {doc.name: number})
		self.assertTrue(frappe.db.exists("JP My Number Access Log", {"my_number": doc.name, "action": "出力"}))

	def make_retired(self, relieving):
		emp = fx.make_employee(joining="2010-04-01")
		emp.update({"status": "Left", "relieving_date": relieving, "jp_separation_type": "自己都合"})
		emp.save()
		return emp

	def test_disposal_due_and_disposal(self):
		"""退職者ごとに廃棄可能日が根拠の書類とともに表示され、廃棄後は復号できず廃棄記録が残る（EMP-66）。"""
		old = self.make_retired("2018-03-31")
		doc = self.register(old.name, fake_number("24682468246"))
		# 扶養控除等申告書：2019-01-11（翌年1月10日の翌日）から7年 → 2026-01-11。雇保4年・社保2年より遅い
		self.assertEqual(getdate(doc.disposal_due), getdate("2026-01-11"))
		self.assertIn("扶養控除等", doc.disposal_basis.split("\n")[1])
		self.assertNotEqual(getdate(doc.disposal_due), getdate("2025-03-31"))  # 「退職後一律7年」ではない
		self.assertIn(doc.name, [r.name for r in mn.get_disposal_due_list("2026-11-15")])

		frappe.set_user(HR)
		with self.assertRaises(frappe.PermissionError):
			mn.dispose(doc.name)
		frappe.set_user(OFFICER)
		mn.dispose(doc.name, "保存期限の経過（テスト）")
		frappe.set_user("Administrator")
		doc.reload()
		self.assertEqual((doc.status, doc.disposed_by, doc.masked_number), ("廃棄済み", OFFICER, "（廃棄済み）"))
		self.assertTrue(doc.disposed_on)
		self.assertFalse(frappe.db.sql("select 1 from `__Auth` where doctype='JP My Number' and name=%s", doc.name))
		with self.assertRaises(frappe.ValidationError):
			self.reveal(OFFICER, doc.name)
		self.assertTrue(frappe.db.exists("JP My Number Access Log", {"my_number": doc.name, "action": "廃棄", "user": OFFICER}))
		self.assertNotIn(doc.name, [r.name for r in mn.get_disposal_due_list("2026-11-15")])

		recent = self.make_retired("2020-03-31")
		doc2 = self.register(recent.name, fake_number("97531975319"))
		self.assertEqual(getdate(doc2.disposal_due), getdate("2028-01-11"))
		frappe.set_user(OFFICER)
		with self.assertRaises(frappe.ValidationError):
			mn.dispose(doc2.name)  # 期限前は廃棄できない
		frappe.set_user("Administrator")

		active = fx.make_employee()
		doc3 = self.register(active.name, fake_number("86420864208"))
		self.assertIsNone(doc3.disposal_due)  # 在籍中（事務の継続中）は算出しない
		active.reload()
		active.update({"status": "Left", "relieving_date": "2026-03-31", "jp_separation_type": "自己都合"})
		active.save()
		self.assertEqual(getdate(frappe.db.get_value("JP My Number", doc3.name, "disposal_due")), getdate("2034-01-11"))

	def test_dependent_number_and_removal(self):
		"""扶養親族の番号は専用 DocType に持ち、扶養から外れた日から廃棄可能日を算出する（EMP-60・66・43）。"""
		emp = fx.make_employee(jp_dependents=[{"dependent_name": "試験 花子", "dependent_kana": "シケン ハナコ", "relation": "配偶者",
			"birth_date": "1992-02-02", "hi_dependent_from": "2025-04-01"}])
		row = emp.jp_dependents[0].name
		dep = self.register(emp.name, fake_number("19283746501"), owner_type="扶養親族", dependent_row=row)
		self.assertEqual(dep.dependent_name, "試験 花子")
		self.assertIsNone(dep.disposal_due)
		emp.reload()
		emp.jp_dependent_change_date = "2026-06-30"
		emp.set("jp_dependents", [])
		emp.save()
		dep.reload()
		self.assertEqual(getdate(dep.work_end_date), getdate("2026-06-30"))
		self.assertEqual(getdate(dep.disposal_due), getdate("2034-01-11"))
