"""マイナンバー（従業員マスタ要件 B-1 v1.0 EMP-60〜67）。

- 番号は JP My Number の Password 型の項目 ``my_number`` に入れ、Frappe の暗号化（frappe.utils.password、
  サイトの encryption_key）で __Auth に保存する。DocType の列・変更履歴（Version）には「*」だけが残る（EMP-60・67）
- 画面の表示は常に全桁マスク（masked_number）。マスクは状態から作る文字列で、保存値には触れない（EMP-62）
- 復号・出力は事務取扱担当者（JP My Number Officer）だけが、理由を入力して行う。System Manager・Administrator も不可（EMP-63・65）
- 登録・復号・変更・出力・廃棄（と拒否）をすべて JP My Number Access Log に追記する（EMP-64）
- 廃棄可能日は書類ごとの保存期限（JP My Number Retention Rule）と事務の終了日から算出する（EMP-66）

技術的な安全性（鍵管理・バックアップからの消去等）は本アプリの機能だけでは担保されない。運用環境に応じて利用者が確認すること。
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_years, getdate, now_datetime, today
from frappe.utils.password import get_decrypted_password

DOCTYPE = "JP My Number"
LOG_DOCTYPE = "JP My Number Access Log"
RULE_DOCTYPE = "JP My Number Retention Rule"
FIELD = "my_number"

MASK = "＊" * 12
DISPOSED_LABEL = "（廃棄済み）"
UNREGISTERED_LABEL = "（未登録）"
ACTIVE, DISPOSED = "有効", "廃棄済み"

REASON_TYPES = (
	"届出書の作成（社会保険）", "届出書の作成（雇用保険）", "源泉徴収票・支払調書等の作成", "扶養控除等申告書の確認",
	"本人確認・登録内容の確認", "その他",
)

START_DAY_AFTER = "事務の終了日の翌日"
START_JAN11 = "事務の終了日の属する年の翌年1月11日（提出期限の属する年の翌年1月10日の翌日）"

# 初期の保存期限（マスタに登録し、会社が変更できる）。値は未確定のため、利用者が法令に照らして確認すること
DEFAULT_RETENTION_RULES = (
	dict(document_type="給与所得者の扶養控除等（異動）申告書", legal_basis="所得税法施行規則76条の3", applies_to="本人・扶養親族",
		start_event=START_JAN11, years=7,
		remarks="提出期限の属する年の翌年1月10日の翌日から7年。最後に提出された申告書は事務の終了年分とみなす"),
	dict(document_type="雇用保険の届出（被保険者に関する書類）の控え", legal_basis="雇用保険法施行規則143条（要確認）", applies_to="本人",
		start_event=START_DAY_AFTER, years=4, remarks="完結の日から4年。完結の日を事務の終了日とみなす（要確認）"),
	dict(document_type="健康保険・厚生年金保険の届出の控え", legal_basis="健康保険法施行規則34条・厚生年金保険法施行規則28条（要確認）",
		applies_to="本人・扶養親族", start_event=START_DAY_AFTER, years=2, remarks="完結の日から2年。完結の日を事務の終了日とみなす（要確認）"),
)


# --- 形式（平成26年総務省令第85号第5条） ---------------------------------------------------------------
def check_digit(first11):
	"""先頭11桁から検査用数字を求める。

	P(n) を検査用数字の1つ左の桁から数えて n 番目の数字、Q(n) を n≦6 のとき n+1、7≦n≦11 のとき n−5 として、
	Σ P(n)×Q(n) を 11 で割った余りが 1 以下なら 0、それ以外は 11 − 余り。
	"""
	total = sum(int(first11[11 - n]) * (n + 1 if n <= 6 else n - 5) for n in range(1, 12))
	remainder = total % 11
	return 0 if remainder <= 1 else 11 - remainder


def normalize_number(value):
	"""全角数字・区切りを除いて12桁の数字にする。形式・検査用数字の誤りは ValueError（番号は文言に含めない）。"""
	from japan_hr_compliance.jp_hr_core.normalize import to_half_digits

	s = "".join(c for c in to_half_digits(str(value or "")) if c not in " -‐－　")
	if not (s.isascii() and s.isdigit()) or len(s) != 12:
		raise ValueError("個人番号は12桁の数字で入力してください。")
	if check_digit(s[:11]) != int(s[11]):
		raise ValueError("個人番号の検査用数字が一致しません。番号を確認してください。")
	return s


def is_dummy(value):
	return bool(value) and set(str(value)) == {"*"}


# --- 権限 ----------------------------------------------------------------------------------------
def is_officer(user=None):
	"""事務取扱担当者か。Administrator は全ロールを持つ扱いになるため明示的に除く（EMP-63）。"""
	from japan_hr_compliance.setup.roles import MY_NUMBER_OFFICER

	user = user or frappe.session.user
	if user in ("Administrator", "Guest"):
		return False
	return MY_NUMBER_OFFICER in frappe.get_roles(user)


def _require_officer(doc=None, action="復号"):
	# 拒否はログに残さない（例外でトランザクションが巻き戻るため。要件のログ対象は復号・変更・出力・廃棄）
	if not is_officer():
		frappe.throw(_("マイナンバーの{0}は事務取扱担当者（JP My Number Officer）だけが行えます。").format(action), frappe.PermissionError)


def _require_reason(reason_type, reason):
	if reason_type not in REASON_TYPES:
		frappe.throw(_("理由の区分を選んでください。"))
	if len((reason or "").strip()) < 2:
		frappe.throw(_("理由（具体的な事務の内容）を入力してください。"))


# --- ログ（EMP-64） ------------------------------------------------------------------------------
def write_log(doc, action, reason_type=None, reason=None):
	log = frappe.get_doc({
		"doctype": LOG_DOCTYPE, "log_datetime": now_datetime(), "user": frappe.session.user, "action": action,
		"my_number": doc.name, "employee": doc.employee, "owner_type": doc.owner_type,
		"reason_type": reason_type, "reason": reason, "ip_address": getattr(frappe.local, "request_ip", None),
	})
	log.flags.jp_log_insert = True
	log.insert(ignore_permissions=True)
	return log


# --- 復号・出力（EMP-63・65） ----------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def reveal(name, reason_type, reason):
	"""番号を復号して返す。事務取扱担当者のみ、理由の入力が必須。復号のたびにログを残す。"""
	doc = frappe.get_doc(DOCTYPE, name)
	_require_officer(doc, "復号")
	_require_reason(reason_type, reason)
	if doc.status == DISPOSED:
		frappe.throw(_("廃棄済みの番号は復号できません。"))
	value = get_decrypted_password(DOCTYPE, doc.name, FIELD, raise_exception=False)
	if not value:
		frappe.throw(_("番号が登録されていません。"))
	write_log(doc, "復号", reason_type, reason)
	return value


def get_numbers_for_output(names, reason_type, reason, purpose):
	"""届出データ等で番号の出力が必要な機能から呼ぶ（EMP-65 の個別の許可）。

	事務取扱担当者の操作と理由入力を条件とし、1件ごとに「出力」のログを残す。標準のエクスポート・
	レポート・API はこの関数を通らないため番号を含まない。

	Returns:
		dict[str, str]: {JP My Number の name: 番号}（廃棄済み・未登録は含めない）
	"""
	_require_officer(action="出力")
	_require_reason(reason_type, reason)
	if not (purpose or "").strip():
		frappe.throw(_("出力先の機能（届出の種類等）を指定してください。"))
	result = {}
	for name in names:
		doc = frappe.get_doc(DOCTYPE, name)
		if doc.status == DISPOSED:
			continue
		value = get_decrypted_password(DOCTYPE, name, FIELD, raise_exception=False)
		if value:
			write_log(doc, "出力", reason_type, f"{reason}（出力先：{purpose}）")
			result[name] = value
	return result


@frappe.whitelist()
def guarded_get_password(doctype, name, fieldname):
	"""frappe.client.get_password の置き換え。標準では System Manager が Password 型を読めるため、
	JP My Number だけは拒否する（EMP-63）。他の DocType は標準の動きのまま。"""
	from frappe.client import get_password

	if doctype == DOCTYPE:
		frappe.throw(_("マイナンバーはこの方法では取得できません。"), frappe.PermissionError)
	return get_password(doctype, name, fieldname)


# --- 廃棄（EMP-66） ------------------------------------------------------------------------------
def ensure_retention_rules():
	for rule in DEFAULT_RETENTION_RULES:
		if not frappe.db.exists(RULE_DOCTYPE, rule["document_type"]):
			frappe.get_doc({"doctype": RULE_DOCTYPE, "enabled": 1, **rule}).insert(ignore_permissions=True)


def rule_due_date(rule, work_end):
	"""書類1つの保存期限の翌日（この日から廃棄できる）。"""
	work_end = getdate(work_end)
	if rule.start_event == START_JAN11:
		start = getdate(f"{work_end.year + 1}-01-11")
	else:
		start = add_days(work_end, 1)
	return getdate(add_years(start, int(rule.years or 0)))


def compute_disposal(owner_type, work_end):
	"""事務の終了日から廃棄可能日と根拠を返す。終了日がなければ (None, "")。

	適用されるすべての書類の期限のうち最も遅い日を廃棄可能日とする（一律の「退職後7年」は使わない）。
	"""
	if not work_end:
		return None, ""
	rules = frappe.get_all(RULE_DOCTYPE, filters={"enabled": 1},
		fields=["document_type", "legal_basis", "applies_to", "start_event", "years"], order_by="document_type asc")
	lines, due = [], None
	for rule in rules:
		if rule.applies_to not in ("本人・扶養親族", owner_type):
			continue
		d = rule_due_date(rule, work_end)
		lines.append((d, f"{rule.document_type}（{rule.legal_basis or '根拠未設定'}、{rule.start_event}から{rule.years}年）：{d} から廃棄可"))
		due = max(due, d) if due else d
	if not lines:
		return None, _("適用される保存期限の規則がありません")
	text = "\n".join(("◎ " if d == due else "・ ") + line for d, line in sorted(lines, key=lambda x: x[0], reverse=True))
	return due, f"事務の終了日：{getdate(work_end)}\n{text}"


def derived_work_end(doc):
	"""事務の終了日（手入力がなければ本人・扶養とも退職日）。扶養から外れた日は異動の記録で work_end_date に入る。"""
	if doc.work_end_date:
		return getdate(doc.work_end_date)
	relieving = frappe.db.get_value("Employee", doc.employee, "relieving_date")
	return getdate(relieving) if relieving else None


def refresh_disposal_for_employee(employee):
	"""退職日の変更などで、従業員の番号の廃棄可能日を算出し直す。"""
	for name in frappe.get_all(DOCTYPE, filters={"employee": employee, "status": ACTIVE}, pluck="name"):
		doc = frappe.get_doc(DOCTYPE, name)
		due, basis = compute_disposal(doc.owner_type, derived_work_end(doc))
		frappe.db.set_value(DOCTYPE, name, {"disposal_due": due, "disposal_basis": basis}, update_modified=False)


def set_dependent_work_end(employee, dependent_row, end_date):
	"""扶養から外れた家族の番号に事務の終了日を入れる（EMP-43 の異動から呼ぶ）。"""
	for name in frappe.get_all(DOCTYPE, filters={"employee": employee, "dependent_row": dependent_row, "status": ACTIVE,
			"work_end_date": ("is", "not set")}, pluck="name"):
		doc = frappe.get_doc(DOCTYPE, name)
		doc.work_end_date = end_date
		doc.flags.jp_system_update = True
		doc.save(ignore_permissions=True)


def get_disposal_due_list(on_date=None, company=None):
	"""廃棄可能日が到来した有効な番号の一覧（期限到来の一覧）。番号そのものは含まない。"""
	filters = {"status": ACTIVE, "disposal_due": ("<=", getdate(on_date or today()))}
	if company:
		filters["company"] = company
	return frappe.get_all(DOCTYPE, filters=filters,
		fields=["name", "employee", "employee_name", "owner_type", "dependent_name", "disposal_due", "disposal_basis"],
		order_by="disposal_due asc")


@frappe.whitelist(methods=["POST"])
def dispose(name, reason=None):
	"""番号を廃棄する（暗号文を削除し、廃棄日・実施者を記録）。廃棄可能日の前は廃棄できない。"""
	doc = frappe.get_doc(DOCTYPE, name)
	_require_officer(doc, "廃棄")
	if doc.status == DISPOSED:
		frappe.throw(_("この番号は廃棄済みです。"))
	if not doc.disposal_due or getdate(doc.disposal_due) > getdate(today()):
		frappe.throw(_("廃棄可能日（{0}）が到来していません。").format(doc.disposal_due or _("未算出")))
	doc.flags.jp_disposing = True
	doc.my_number = None  # 空にすると Frappe が __Auth の暗号文を削除する
	doc.status = DISPOSED
	doc.disposed_on = today()
	doc.disposed_by = frappe.session.user
	doc.save(ignore_permissions=True)
	write_log(doc, "廃棄", "廃棄", reason or _("保存期限の経過による廃棄"))
	return doc.name


def dependent_options(employee):
	"""扶養親族の選択肢（行 ID と氏名）。"""
	return frappe.get_all("JP Employee Dependent", filters={"parent": employee, "parenttype": "Employee"},
		fields=["name", "dependent_name", "relation"], order_by="idx asc")


@frappe.whitelist()
def get_dependent_options(employee):
	frappe.has_permission("Employee", "read", employee, throw=True)
	return dependent_options(employee)
