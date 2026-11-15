"""従業員ポータルからの住所・振込口座・扶養の変更申請（従業員マスタ要件 B-1 v1.0 EMP-86）。

一般従業員は本人の分だけを申請でき、人事担当（口座は給与担当も可）の承認で Employee に反映する。
承認前の申請内容はマスタに反映しない。申請・承認・却下の記録は削除できない。

申請内容（payload）の形：
- 住所：{"valid_from", "postal_code", "prefecture", "municipality", "lg_code", "address_line", "building"}
- 振込口座：{"accounts": [{"priority", "bank_code", "bank_name", "branch_code", "branch_name", "account_type",
  "account_no", "holder_kana", "fixed_amount"}, ...]}（口座の表をこの内容に置き換える）
- 扶養親族：{"action": "追加" | "変更" | "削除", "row": 行 ID（変更・削除）, "values": {...}, "change_date": 異動日}
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from japan_hr_compliance.setup.roles import HR_USER, PAYROLL_USER

DOCTYPE = "JP Employee Change Request"
ADDRESS, BANK, DEPENDENT = "住所", "振込口座", "扶養親族"
PENDING, APPROVED, REJECTED = "申請中", "承認", "却下"

ADDRESS_FIELDS = ("valid_from", "postal_code", "prefecture", "municipality", "lg_code", "address_line", "building")
BANK_FIELDS = ("priority", "bank_code", "bank_name", "branch_code", "branch_name", "account_type", "account_no", "holder_kana", "fixed_amount")
DEPENDENT_FIELDS = ("dependent_name", "dependent_kana", "relation", "birth_date", "gender", "cohabiting", "non_resident",
	"disability_class", "hi_dependent_from", "hi_dependent_to", "nenkin_3go")

APPROVER_ROLES = {
	ADDRESS: (HR_USER, "HR User", "HR Manager"),
	DEPENDENT: (HR_USER, "HR User", "HR Manager"),
	BANK: (HR_USER, PAYROLL_USER, "HR User", "HR Manager"),
}


def _pick(data, fields):
	return {k: data.get(k) for k in fields if data.get(k) not in (None, "")}


def clean_payload(request_type, data):
	data = frappe.parse_json(data) if isinstance(data, str) else (data or {})
	if request_type == ADDRESS:
		payload = _pick(data, ADDRESS_FIELDS)
		if not payload.get("valid_from") or not payload.get("municipality"):
			frappe.throw(_("住所の適用開始日（転居日）と市区町村を入力してください。"))
		return payload
	if request_type == BANK:
		accounts = [_pick(a, BANK_FIELDS) for a in data.get("accounts") or []]
		if not accounts:
			frappe.throw(_("振込口座を入力してください。"))
		return {"accounts": accounts}
	if request_type == DEPENDENT:
		action = data.get("action")
		if action not in ("追加", "変更", "削除"):
			frappe.throw(_("扶養親族の申請の種類（追加・変更・削除）を選んでください。"))
		if action != "追加" and not data.get("row"):
			frappe.throw(_("変更・削除する扶養親族を選んでください。"))
		if action == "追加" and not (data.get("values") or {}).get("dependent_name"):
			frappe.throw(_("追加する扶養親族の氏名を入力してください。"))
		return {"action": action, "row": data.get("row"), "values": _pick(data.get("values") or {}, DEPENDENT_FIELDS),
			"change_date": data.get("change_date")}
	frappe.throw(_("申請の種類が不明です。"))


def summarize(request_type, payload):
	if request_type == ADDRESS:
		return f"{payload.get('valid_from')} から：〒{payload.get('postal_code') or ''} {payload.get('prefecture') or ''}" \
			f"{payload.get('municipality') or ''}{payload.get('address_line') or ''} {payload.get('building') or ''}".strip()
	if request_type == BANK:
		return "\n".join(f"{a.get('priority')}. {a.get('bank_code')} {a.get('bank_name') or ''} {a.get('branch_code')} "
			f"{a.get('account_type') or ''} {a.get('account_no')}" + (f"（定額 {a.get('fixed_amount')} 円）" if a.get("fixed_amount") else "（残額）")
			for a in payload["accounts"])
	values = payload.get("values") or {}
	return f"{payload['action']}：{values.get('dependent_name') or payload.get('row') or ''}（異動日 {payload.get('change_date') or '未入力'}）"


def create_request(employee, request_type, data):
	payload = clean_payload(request_type, data)
	doc = frappe.get_doc({
		"doctype": DOCTYPE, "employee": employee, "request_type": request_type, "status": PENDING,
		"requested_by": frappe.session.user, "requested_on": now_datetime(),
		"payload": json.dumps(payload, ensure_ascii=False, default=str), "summary": summarize(request_type, payload),
	})
	doc.flags.jp_api_insert = True
	doc.insert(ignore_permissions=True)
	return doc


@frappe.whitelist(methods=["POST"])
def submit_change_request(request_type, data):
	"""従業員ポータルから本人の変更を申請する。"""
	from japan_hr_compliance.jp_hr_core.consent import employee_for_session

	if frappe.session.user == "Guest":
		frappe.throw(_("ログインしてください。"), frappe.PermissionError)
	return create_request(employee_for_session(), request_type, data).name


def _check_approver(request_type):
	roles = set(frappe.get_roles())
	if frappe.session.user != "Administrator" and not roles & set(APPROVER_ROLES[request_type]):
		frappe.throw(_("この申請を承認・却下する権限がありません。"), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def approve(name):
	"""申請を承認し、Employee に反映する。"""
	doc = frappe.get_doc(DOCTYPE, name)
	_check_approver(doc.request_type)
	if doc.status != PENDING:
		frappe.throw(_("この申請は処理済みです（{0}）。").format(doc.status))
	apply_to_employee(doc)
	doc.flags.jp_decision = True
	doc.status, doc.decided_by, doc.decided_on = APPROVED, frappe.session.user, now_datetime()
	doc.save(ignore_permissions=True)
	return doc.name


@frappe.whitelist(methods=["POST"])
def reject(name, reason):
	doc = frappe.get_doc(DOCTYPE, name)
	_check_approver(doc.request_type)
	if doc.status != PENDING:
		frappe.throw(_("この申請は処理済みです（{0}）。").format(doc.status))
	if not (reason or "").strip():
		frappe.throw(_("却下の理由を入力してください。"))
	doc.flags.jp_decision = True
	doc.status, doc.decided_by, doc.decided_on, doc.reject_reason = REJECTED, frappe.session.user, now_datetime(), reason
	doc.save(ignore_permissions=True)
	return doc.name


def apply_to_employee(req):
	payload = json.loads(req.payload)
	emp = frappe.get_doc("Employee", req.employee)
	if req.request_type == ADDRESS:
		emp.append("jp_addresses", payload)
	elif req.request_type == BANK:
		emp.set("jp_bank_accounts", [])
		for row in payload["accounts"]:
			emp.append("jp_bank_accounts", row)
	else:
		action, values = payload["action"], payload.get("values") or {}
		if payload.get("change_date"):
			emp.jp_dependent_change_date = payload["change_date"]
		if action == "追加":
			emp.append("jp_dependents", values)
		else:
			row = next((r for r in emp.jp_dependents if r.name == payload["row"]), None)
			if not row:
				frappe.throw(_("申請の対象の扶養親族が見つかりません。"))
			if action == "変更":
				row.update(values)
			else:
				emp.jp_dependents.remove(row)
	emp.flags.jp_change_request = req.name
	emp.save(ignore_permissions=True)


def get_pending_requests(employee):
	return frappe.get_all(DOCTYPE, filters={"employee": employee}, fields=["name", "request_type", "status", "summary",
		"requested_on", "reject_reason"], order_by="requested_on desc", limit=20)
