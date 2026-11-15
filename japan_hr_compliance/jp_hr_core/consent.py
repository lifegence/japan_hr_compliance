"""口座振込の同意・電子交付の承諾（従業員マスタ要件 B-1 v1.0 EMP-34〜37）。

同意・承諾は項目ごとに個別に記録する（一括同意はしない）。記録は追記のみで、
撤回は「撤回」の記録を加えて表す。ある日時点の状態は、その日時以前で最も新しい記録で決まる。
"""

import datetime

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, now_datetime

from japan_hr_compliance.jp_hr_core.address import active_employees

PAYSLIP = "給与明細の電子交付"
WITHHOLDING_SLIP = "源泉徴収票の電子交付"
BANK_TRANSFER = "口座振込"
LABOR_NOTICE = "労働条件通知書の電子交付"
CONSENT_TYPES = (PAYSLIP, WITHHOLDING_SLIP, BANK_TRANSFER, LABOR_NOTICE)
AGREE, WITHDRAW = "同意", "撤回"

# 同意文面と版。文面を変えたら版を上げる（記録には版が残る）
CONSENT_TEXTS = {
	PAYSLIP: ("2026-11-v1", "給与明細（給与等の支払明細書）を、書面に代えて電磁的方法（従業員ポータルでの閲覧・ダウンロード）で受け取ることを承諾します。承諾はいつでも撤回でき、撤回後は書面で交付されます。"),
	WITHHOLDING_SLIP: ("2026-11-v1", "給与所得の源泉徴収票を、書面に代えて電磁的方法（従業員ポータルでの閲覧・ダウンロード）で受け取ることを承諾します。承諾はいつでも撤回できます。"),
	BANK_TRANSFER: ("2026-11-v1", "賃金を、下記の私が指定する預金口座への振込により支払うことに同意します。対象の賃金・金額と開始時期は下記のとおりです。同意はいつでも撤回できます。"),
	LABOR_NOTICE: ("2026-11-v1", "労働条件通知書を、書面に代えて電子メール等の電磁的方法（書面に出力できる方法）で受け取ることを希望します。"),
}


def _at(at):
	"""判定日時。日付だけが渡された場合はその日の終わりまでの記録を見る。"""
	if not at:
		return now_datetime()
	if (isinstance(at, str) and len(at.strip()) == 10) or (isinstance(at, datetime.date) and not isinstance(at, datetime.datetime)):
		return datetime.datetime.combine(getdate(at), datetime.time(23, 59, 59))
	return get_datetime(at)


def get_consent_record(employee, consent_type, at=None):
	"""指定日時（既定は現在）以前で最も新しい記録。なければ None。"""
	rows = frappe.get_all(
		"JP Employee Consent",
		filters={"employee": employee, "consent_type": consent_type, "consent_datetime": ("<=", _at(at))},
		fields=["name", "status", "consent_datetime", "method", "text_version", "wage_scope", "start_period", "accounts_snapshot"],
		order_by="consent_datetime desc, creation desc", limit=1,
	)
	return rows[0] if rows else None


def has_consent(employee, consent_type, at=None):
	"""指定日時に同意・承諾が有効か（最新の記録が「同意」）。"""
	record = get_consent_record(employee, consent_type, at)
	return bool(record and record.status == AGREE)


def get_consent_status(employee, at=None):
	"""項目ごとの状態 {種類: "同意" | "撤回" | None}。"""
	return {t: (r.status if (r := get_consent_record(employee, t, at)) else None) for t in CONSENT_TYPES}


def accounts_snapshot(employee):
	rows = frappe.get_all("JP Employee Bank Account", filters={"parent": employee, "parenttype": "Employee"},
		fields=["priority", "bank_code", "bank_name", "branch_code", "branch_name", "account_type", "account_no", "holder_kana", "fixed_amount"],
		order_by="priority asc")
	lines = []
	for r in rows:
		amount = f"定額 {int(flt(r.fixed_amount)):,} 円" if flt(r.fixed_amount) else "残額"
		lines.append(f"{r.priority}. {r.bank_code} {r.bank_name or ''} {r.branch_code} {r.branch_name or ''} {r.account_type} "
			f"{r.account_no} {r.holder_kana}（{amount}）")
	return "\n".join(lines)


def record_consent(employee, consent_type, status=AGREE, method="Web", text_version=None, **values):
	"""同意・承諾（または撤回）の記録を1件追加して返す。権限の確認は呼び出し側で行う。"""
	doc = frappe.get_doc({
		"doctype": "JP Employee Consent", "employee": employee, "consent_type": consent_type, "status": status,
		"method": method, "text_version": text_version or CONSENT_TEXTS[consent_type][0],
		"consent_datetime": values.pop("consent_datetime", None) or now_datetime(), **values,
	})
	doc.insert(ignore_permissions=True)
	return doc


@frappe.whitelist(methods=["POST"])
def register_paper_consent(employee, consent_type, attachment, status=AGREE, consent_datetime=None, wage_scope=None,
		start_period=None, text_version=None, remarks=None):
	"""書面で取得した同意・承諾（または撤回）を人事・給与担当が代理登録する（EMP-38）。方法は「書面」で記録する。"""
	frappe.has_permission("JP Employee Consent", "create", throw=True)
	if consent_type not in CONSENT_TYPES or status not in (AGREE, WITHDRAW):
		frappe.throw(_("同意・承諾の種類または区分が不明です。"))
	if consent_type == BANK_TRANSFER and status == AGREE and not (wage_scope or "").strip():
		frappe.throw(_("口座振込の対象となる賃金・金額を入力してください。"))
	return record_consent(employee, consent_type, status, method="書面", text_version=text_version or "書面",
		attachment=attachment, consent_datetime=consent_datetime, wage_scope=wage_scope, start_period=start_period,
		remarks=remarks).name


# --- 振込データ（PY-010）への引き渡し -------------------------------------------------------------
def get_transfer_allocation(employee, net_pay, at=None):
	"""差引支給額を口座ごとに分ける（EMP-34・35）。口座振込の同意がなければ None（現金払いの対象）。

	振込順位の小さい口座から定額を振り込み、残りを定額のない口座（残額の振込先）に振り込む。
	差引支給額が定額の合計に満たない場合は、順位の小さい口座から支給額の範囲で振り込む。

	Returns:
		list[frappe._dict] | None: 口座の項目に ``amount`` を加えたもの（金額 0 の口座は含めない）
	"""
	if not has_consent(employee, BANK_TRANSFER, at):
		return None
	rows = frappe.get_all("JP Employee Bank Account", filters={"parent": employee, "parenttype": "Employee"},
		fields=["priority", "bank_code", "bank_name", "branch_code", "branch_name", "account_type", "account_no", "holder_kana", "fixed_amount"],
		order_by="priority asc")
	if not rows:
		return None
	remaining = flt(net_pay)
	result = []
	for row in rows:
		amount = min(flt(row.fixed_amount), remaining) if flt(row.fixed_amount) else remaining
		remaining -= amount
		if amount > 0:
			row.amount = amount
			result.append(row)
	return result


def split_by_transfer_consent(employees, at=None):
	"""従業員を振込の対象と現金払いの対象に分ける（EMP-35）。同意のない従業員は振込データに含めない。"""
	transfer, cash = [], []
	for employee in employees:
		(transfer if has_consent(employee, BANK_TRANSFER, at) else cash).append(employee)
	return {"transfer": transfer, "cash": cash}


def get_paper_delivery_employees(company=None, date=None, consent_type=PAYSLIP):
	"""書面交付の対象者（電子交付の承諾がない・撤回した在籍者）。給与明細の交付（B-8）に引き渡す（EMP-37）。"""
	date = getdate(date) if date else getdate()
	return [e for e in active_employees(date, company) if not has_consent(e, consent_type, date)]


# --- 従業員ポータル（EMP-36） ---------------------------------------------------------------------
def employee_for_session():
	employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": ("!=", "Left")}, "name")
	if not employee:
		frappe.throw(_("ログイン中のユーザーに対応する従業員が見つかりません。"), frappe.PermissionError)
	return employee


@frappe.whitelist()
def submit_portal_consents(choices, wage_scope=None, start_period=None):
	"""従業員ポータルの同意画面から、項目ごとの選択を記録する。

	Args:
		choices: {同意の種類: "同意" | "撤回" | ""}（JSON 文字列可）。空の項目は記録しない。
			現在の状態と同じ選択（同意済みに「同意」など）も記録しない。
		wage_scope, start_period: 口座振込に同意する場合の対象賃金・金額と開始希望時期

	Returns:
		list[str]: 作成した記録の name
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("ログインしてください。"), frappe.PermissionError)
	choices = frappe.parse_json(choices) if isinstance(choices, str) else (choices or {})
	employee = employee_for_session()
	created = []
	for consent_type, choice in choices.items():
		if consent_type not in CONSENT_TYPES or choice not in (AGREE, WITHDRAW):
			continue
		if has_consent(employee, consent_type) == (choice == AGREE):
			continue
		extra = {}
		if consent_type == BANK_TRANSFER and choice == AGREE:
			if not (wage_scope or "").strip():
				frappe.throw(_("口座振込の対象となる賃金・金額を入力してください。"))
			extra = {"wage_scope": wage_scope, "start_period": start_period}
		created.append(record_consent(employee, consent_type, choice, method="Web", **extra).name)
	return created
