"""扶養親族（従業員マスタ要件 B-1 v1.0 EMP-40〜42）。区分の判定規則・所得要件の値は B-5・B-6 のマスタに従い、ここでは持たない。"""

import frappe
from frappe.utils import getdate


def age_on(birth_date, on_date):
	"""on_date 時点の満年齢。"""
	b, d = getdate(birth_date), getdate(on_date)
	return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def get_dependents(employee, on_date=None):
	"""扶養親族の一覧。on_date を渡すと各行に ``age``（その日の満年齢）を加える。"""
	rows = frappe.get_all("JP Employee Dependent", filters={"parent": employee, "parenttype": "Employee"},
		fields=["*"], order_by="idx asc")
	if on_date:
		for row in rows:
			row.age = age_on(row.birth_date, on_date) if row.birth_date else None
	return rows


def get_hi_dependents_at(employee, date):
	"""指定日に健康保険の被扶養者である家族（認定日 ≦ date ≦ 解除日）。"""
	date = getdate(date)
	return [
		r for r in get_dependents(employee, date)
		if r.hi_dependent_from and getdate(r.hi_dependent_from) <= date and (not r.hi_dependent_to or getdate(r.hi_dependent_to) >= date)
	]


def get_dependent_incomes(employee, tax_year):
	"""年分の所得見積と税の区分（EMP-41）。扶養人数の算出は B-6 で行う。"""
	return frappe.get_all("JP Dependent Income", filters={"parent": employee, "parenttype": "Employee", "tax_year": tax_year},
		fields=["dependent_name", "tax_year", "estimated_income", "tax_class"], order_by="idx asc")


# --- 扶養の異動（EMP-43） --------------------------------------------------------------------------
HI_FIELDS = ("hi_dependent_from", "hi_dependent_to", "nenkin_3go")
TAX_FIELDS = ("relation", "birth_date", "cohabiting", "non_resident", "disability_class")
FIELD_LABELS = {
	"hi_dependent_from": "被扶養者の認定日", "hi_dependent_to": "被扶養者の解除日", "nenkin_3go": "第3号被保険者",
	"relation": "続柄", "birth_date": "生年月日", "cohabiting": "同居", "non_resident": "国外居住", "disability_class": "障害の区分",
	"dependent_name": "氏名",
}


def _v(row, field):
	value = row.get(field)
	return str(value) if value not in (None, "", 0) else ""


def diff_dependents(before_rows, after_rows):
	"""扶養親族の表の変更を (種類, 行, 変更内容, 健保に関わる, 税に関わる) の一覧にする。"""
	before = {r.name: r for r in before_rows}
	after = {r.name: r for r in after_rows}
	changes = []
	for name, row in after.items():
		if name not in before:
			changes.append(("追加", row, f"{row.dependent_name}（{row.relation or ''}）を追加", bool(row.hi_dependent_from), True))
			continue
		old = before[name]
		fields = [f for f in HI_FIELDS + TAX_FIELDS + ("dependent_name",) if _v(old, f) != _v(row, f)]
		if fields:
			detail = "、".join(f"{FIELD_LABELS[f]} {_v(old, f) or '—'}→{_v(row, f) or '—'}" for f in fields)
			changes.append(("区分変更", row, detail, any(f in HI_FIELDS for f in fields), any(f in TAX_FIELDS for f in fields)))
	for name, row in before.items():
		if name not in after:
			changes.append(("削除", row, f"{row.dependent_name}（{row.relation or ''}）を削除", bool(row.hi_dependent_from), True))
	return changes


def record_dependent_changes(doc, before):
	"""Employee の保存時に扶養の異動を記録し、手続きタスク（EMP-80）と源泉の再計算（B-6）を起動する。

	異動日は Employee の「扶養の異動日」（jp_dependent_change_date）、なければ今日。記録の後に異動日の欄は空に戻す。
	源泉の扶養人数の再計算（B-6）は hooks の ``jp_on_dependent_change`` に登録された関数を呼ぶ（引数は異動の記録）。
	"""
	from frappe.utils import today

	from japan_hr_compliance.jp_hr_core import my_number, procedures

	if before is None:
		return []
	changes = diff_dependents(before.get("jp_dependents") or [], doc.get("jp_dependents") or [])
	if not changes:
		if doc.get("jp_dependent_change_date"):
			frappe.db.set_value("Employee", doc.name, "jp_dependent_change_date", None, update_modified=False)
		return []
	change_date = getdate(doc.get("jp_dependent_change_date") or today())
	records = []
	for change_type, row, detail, hi_related, tax_related in changes:
		rec = frappe.get_doc({
			"doctype": "JP Dependent Change", "employee": doc.name, "change_date": change_date, "change_type": change_type,
			"dependent_name": row.dependent_name, "dependent_row": row.name, "details": detail, "hi_related": 1 if hi_related else 0,
			"tax_related": 1 if tax_related else 0, "change_request": doc.flags.jp_change_request,
		})
		rec.insert(ignore_permissions=True)
		records.append(rec)
		if change_type == "削除":
			# 扶養から外れた家族の番号は、この日を事務の終了日として廃棄可能日を算出する（EMP-66）
			my_number.set_dependent_work_end(doc.name, row.name, change_date)
		procedures.create_tasks(doc.name, procedures.DEPENDENT, context={
			"change_date": change_date, "hi_related": hi_related, "tax_related": tax_related, "scope": f"扶養異動:{rec.name}",
		}, reference=("JP Dependent Change", rec.name))
		for method in frappe.get_hooks("jp_on_dependent_change"):
			frappe.get_attr(method)(rec)
	frappe.db.set_value("Employee", doc.name, "jp_dependent_change_date", None, update_modified=False)
	return records
