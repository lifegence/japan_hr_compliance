"""労働者名簿（JP）（従業員マスタ要件 B-1 v1.0 EMP-45・46）。

記載事項（労基法107条、労基則53条）：①氏名 ②生年月日 ③履歴 ④性別 ⑤住所 ⑥従事する業務の種類
⑦雇入れの年月日 ⑧退職の年月日及びその事由（解雇の理由を含む） ⑨死亡の年月日及びその原因。

調製は事業場ごと。事業場は各従業員の基準日（在籍者は期間の終了日または今日、退職者は退職日）の
雇用契約から決める。PDF・CSV は標準のレポート画面の印刷・エクスポートで出力する。
業務の種類（⑥）は常時使用する労働者数にかかわらず常に出力する。
"""

import frappe

from japan_hr_compliance.utils.print_fonts import style_block
from frappe import _
from frappe.utils import getdate, today

from japan_hr_compliance.jp_hr_core.address import get_address_at
from japan_hr_compliance.jp_hr_core.contract import get_contract_at, get_contract_history

GENDER_LABELS = {"Male": "男", "Female": "女"}

# 記載事項 → 空欄チェックに使う列
REQUIRED_ITEMS = (
	("① 氏名", "employee_name"), ("② 生年月日", "date_of_birth"), ("④ 性別", "gender"), ("⑤ 住所", "address"),
	("⑥ 業務の種類", "job_name"), ("⑦ 雇入れの年月日", "date_of_joining"),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data, warnings = get_roster(filters)
	return get_columns(), data, warnings_html(warnings)


def get_columns():
	return [
		{"fieldname": "employee", "label": _("従業員"), "fieldtype": "Link", "options": "Employee", "width": 120},
		{"fieldname": "workplace", "label": _("事業場"), "fieldtype": "Link", "options": "JP Workplace", "width": 140},
		{"fieldname": "employee_name", "label": _("① 氏名"), "fieldtype": "Data", "width": 130},
		{"fieldname": "name_kana", "label": _("① 氏名（カナ）"), "fieldtype": "Data", "width": 150},
		{"fieldname": "date_of_birth", "label": _("② 生年月日"), "fieldtype": "Date", "width": 100},
		{"fieldname": "history", "label": _("③ 履歴"), "fieldtype": "Small Text", "width": 320},
		{"fieldname": "gender", "label": _("④ 性別"), "fieldtype": "Data", "width": 60},
		{"fieldname": "address", "label": _("⑤ 住所"), "fieldtype": "Data", "width": 280},
		{"fieldname": "job_name", "label": _("⑥ 従事する業務の種類"), "fieldtype": "Data", "width": 150},
		{"fieldname": "date_of_joining", "label": _("⑦ 雇入れの年月日"), "fieldtype": "Date", "width": 110},
		{"fieldname": "relieving_date", "label": _("⑧ 退職の年月日"), "fieldtype": "Date", "width": 110},
		{"fieldname": "separation", "label": _("⑧ 退職の事由"), "fieldtype": "Small Text", "width": 220},
		{"fieldname": "death_date", "label": _("⑨ 死亡の年月日"), "fieldtype": "Date", "width": 110},
		{"fieldname": "death_cause", "label": _("⑨ 死亡の原因"), "fieldtype": "Small Text", "width": 180},
	]


def get_employees(filters):
	conditions = {"company": filters.company}
	if filters.employee:
		conditions["name"] = filters.employee
	rows = frappe.get_all("Employee", filters=conditions, fields=[
		"name", "employee_name", "first_name", "last_name", "jp_last_name_kana", "jp_first_name_kana", "date_of_birth", "gender",
		"date_of_joining", "relieving_date", "status", "jp_separation_type", "jp_separation_reason", "jp_dismissal_reason",
		"jp_death_date", "jp_death_cause",
	], order_by="date_of_joining asc, name asc")
	from_date = getdate(filters.from_date) if filters.from_date else None
	to_date = getdate(filters.to_date) if filters.to_date else None
	result = []
	for e in rows:
		retired = bool(e.relieving_date)
		if filters.status == "在籍者" and retired:
			continue
		if filters.status == "退職者" and not retired:
			continue
		# 期間内に在籍した従業員だけを出す
		if to_date and e.date_of_joining and getdate(e.date_of_joining) > to_date:
			continue
		if from_date and e.relieving_date and getdate(e.relieving_date) < from_date:
			continue
		result.append(e)
	return result


def reference_date(employee, filters):
	if employee.relieving_date:
		return getdate(employee.relieving_date)
	return getdate(filters.to_date or today())


def build_history(employee, include_external=False):
	"""③ 履歴：雇入れ以降の事業場・部署・業務・雇用区分の変更を日付順に並べる（EMP-46）。"""
	lines = []
	contracts = get_contract_history(employee)
	labels = (("workplace", "事業場"), ("department", "部署"), ("job_category", "業務"), ("employment_class", "雇用区分"))
	for prev, cur in zip(contracts, contracts[1:]):
		changes = [f"{label} {prev.get(f) or '—'}→{cur.get(f) or '—'}" for f, label in labels if (prev.get(f) or "") != (cur.get(f) or "")]
		if changes:
			lines.append(f"{getdate(cur.valid_from)} " + "、".join(changes))
	if include_external:
		for row in frappe.get_all("Employee External Work History", filters={"parent": employee, "parenttype": "Employee"},
				fields=["company_name", "designation", "total_experience"], order_by="idx asc"):
			lines.insert(0, "入社前：" + " ".join(x for x in (row.company_name, row.designation) if x))
	return lines


def separation_text(e):
	if not e.relieving_date and not e.jp_separation_type:
		return ""
	parts = [e.jp_separation_type or ""]
	if e.jp_separation_reason:
		parts.append(e.jp_separation_reason)
	if e.jp_separation_type == "解雇" and e.jp_dismissal_reason:
		parts.append(f"解雇の理由：{e.jp_dismissal_reason}")
	return "／".join(p for p in parts if p)


def get_roster(filters):
	include_external = bool(frappe.db.get_single_value("JP HR Settings", "roster_include_external_history"))
	data, warnings = [], []
	for e in get_employees(filters):
		ref = reference_date(e, filters)
		contract = get_contract_at(e.name, ref)
		if not contract:
			# 退職日に契約がない等。最後の契約を使う
			history = get_contract_history(e.name)
			contract = history[-1] if history else None
		workplace = contract.workplace if contract else None
		if filters.workplace and workplace != filters.workplace:
			continue
		if contract and contract.employment_class == "日雇い":
			continue  # 日々雇い入れられる者は名簿の対象外（労基法107条1項）
		address = get_address_at(e.name, ref)
		job_name = frappe.db.get_value("JP Job Category", contract.job_category, "job_name") if contract and contract.job_category else None
		row = frappe._dict({
			"employee": e.name, "workplace": workplace, "employee_name": e.employee_name,
			"name_kana": "　".join(x for x in (e.jp_last_name_kana, e.jp_first_name_kana) if x),
			"date_of_birth": e.date_of_birth, "history": "\n".join(build_history(e.name, include_external)),
			"gender": GENDER_LABELS.get(e.gender, e.gender), "address": address.full_address if address else "",
			"job_name": job_name, "date_of_joining": e.date_of_joining, "relieving_date": e.relieving_date,
			"separation": separation_text(e), "death_date": e.jp_death_date, "death_cause": e.jp_death_cause,
		})
		data.append(row)
		missing = [label for label, field in REQUIRED_ITEMS if not row.get(field)]
		if e.relieving_date and not row.separation:
			missing.append("⑧ 退職の事由")
		if e.jp_separation_type == "死亡" and not (row.death_date and row.death_cause):
			missing.append("⑨ 死亡の年月日・原因")
		if missing:
			warnings.append({"employee": e.name, "employee_name": e.employee_name, "missing": missing})
	return data, warnings


def warnings_html(warnings):
	if not warnings:
		return None
	items = "".join(
		f"<li>{frappe.utils.escape_html(w['employee'])} {frappe.utils.escape_html(w['employee_name'] or '')}："
		f"{frappe.utils.escape_html('、'.join(w['missing']))}</li>" for w in warnings
	)
	return f"<div><b>{_('記載事項が空欄の従業員')}（{len(warnings)}）</b><ul>{items}</ul></div>"


# --- 電磁的記録による保存（EMP-91）：臨検時に指定した従業員・期間の名簿を即時に PDF で出す -------------------
def roster_html(filters):
	filters = frappe._dict(filters or {})
	columns, data, message = execute(filters)[:3]
	esc = frappe.utils.escape_html
	head = "".join(f"<th>{esc(c['label'])}</th>" for c in columns)
	body = "".join(
		"<tr>" + "".join(f"<td style=\"white-space: pre-wrap\">{esc(str(row.get(c['fieldname']) or ''))}</td>" for c in columns) + "</tr>"
		for row in data)
	period = f"{filters.from_date or ''}〜{filters.to_date or ''}" if (filters.from_date or filters.to_date) else ""
	title = esc(f"労働者名簿 {filters.company or ''} {filters.workplace or ''} {period}".strip())
	return ('<html><head><meta charset="utf-8">'
		+ style_block("body{font-size:8pt}table{border-collapse:collapse;width:100%}td,th{border:1px solid #000;padding:2px;vertical-align:top}")
		+ "</head>"
		f"<body><h3>{title}</h3><p>出力日時：{frappe.utils.now()}</p><table><tr>{head}</tr>{body}</table>{message or ''}</body></html>")


def roster_pdf(filters):
	from frappe.utils.pdf import get_pdf

	return get_pdf(roster_html(filters), {"orientation": "Landscape", "page-size": "A4"})


@frappe.whitelist()
def download_roster_pdf(filters):
	"""指定した従業員・事業場・期間の労働者名簿を PDF で返す（EMP-91）。"""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else filters
	if not frappe.get_doc("Report", "JP Worker Roster").is_permitted():
		frappe.throw(_("労働者名簿を出力する権限がありません。"), frappe.PermissionError)
	frappe.local.response.filename = "worker_roster.pdf"
	frappe.local.response.filecontent = roster_pdf(filters)
	frappe.local.response.type = "pdf"
