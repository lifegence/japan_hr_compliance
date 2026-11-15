"""Employee の検証・削除制限（従業員マスタ要件 B-1 v1.0）。hrms／ERPNext の Employee は改変せず doc_events で拡張する。"""

import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import getdate

from japan_hr_compliance.jp_hr_core import normalize

INSURANCE_NUMBERS = (
	# fieldname, label, digits（None は桁数で制限しない）
	("jp_basic_pension_no", "基礎年金番号", 10),
	("jp_emp_ins_no", "雇用保険被保険者番号", 11),
	("jp_hi_insurer_no", "健康保険の保険者番号", 8),
	("jp_hi_symbol", "健康保険の記号", None),
	("jp_hi_number", "健康保険の番号", None),
	("jp_hi_branch_no", "健康保険の枝番", 2),
)
# 被保険者でない場合の理由（EMP-12）→ 対象外とみなす制度。判定規則そのものは B-5。兼務役員は自動で除外しない
SI_EXEMPT_SCHEMES = {
	"適用除外（健康保険・厚生年金）": {"健康保険", "厚生年金"},
	"適用除外（雇用保険）": {"雇用保険"},
	"適用除外（すべて）": {"健康保険", "厚生年金", "雇用保険"},
	"役員（雇用保険の対象外）": {"雇用保険"},
	"兼務役員（雇保は労働者部分で判定）": set(),
	"70歳以上（厚生年金の資格喪失）": {"厚生年金"},
	"75歳以上（後期高齢者医療）": {"健康保険"},
	"その他": set(),
}

RESIDENCE_CARD = re.compile(r"^[A-Z]{2}[0-9]{8}[A-Z]{2}$")

QUALIFICATION_DATES = (
	("jp_hi_acquired", "jp_hi_lost", "健康保険"),
	("jp_pi_acquired", "jp_pi_lost", "厚生年金"),
	("jp_ei_acquired", "jp_ei_lost", "雇用保険"),
)


def _throw_value_error(fn, *args):
	try:
		return fn(*args)
	except ValueError as e:
		frappe.throw(str(e))


def is_retired(employee):
	"""退職者（status=Left または退職日あり）か。退職者の記録は削除させない（EMP-47）。"""
	values = frappe.db.get_value("Employee", employee, ["status", "relieving_date"], as_dict=True)
	return bool(values and (values.status == "Left" or values.relieving_date))


def validate(doc, method=None):
	if doc.flags.jp_contract_sync:
		return
	validate_kana(doc)
	validate_former_name(doc)
	validate_residence(doc)
	validate_employee_number(doc)
	validate_insurance(doc)
	validate_addresses(doc)
	validate_bank_accounts(doc)
	validate_dependents(doc)
	validate_separation(doc)
	validate_retained_rows(doc)
	validate_contract_period(doc)


def validate_kana(doc):
	"""氏名カナを全角カナに正規化する（EMP-01）。必須は Custom Field の reqd で担保する。"""
	doc.jp_last_name_kana = _throw_value_error(normalize.to_zenkaku_kana, doc.jp_last_name_kana, "姓（カナ）")
	doc.jp_first_name_kana = _throw_value_error(normalize.to_zenkaku_kana, doc.jp_first_name_kana, "名（カナ）")


def validate_former_name(doc):
	"""旧姓（通称）（EMP-02）。旧姓がなければ通称の表示もしない。"""
	doc.jp_former_name = (doc.jp_former_name or "").strip() or None
	if not doc.jp_former_name:
		doc.jp_display_former_name = 0


def validate_residence(doc):
	"""外国人の在留情報（EMP-04）。在留カード番号は英字2桁＋数字8桁＋英字2桁（大文字に揃える）。"""
	if doc.jp_residence_card_no:
		value = unicodedata.normalize("NFKC", "".join(doc.jp_residence_card_no.split())).upper()
		if not RESIDENCE_CARD.match(value):
			frappe.throw(_("在留カード番号は英字2桁＋数字8桁＋英字2桁の12文字です。"))
		doc.jp_residence_card_no = value
	if doc.jp_romaji_name:
		doc.jp_romaji_name = " ".join(doc.jp_romaji_name.split()).upper()


def exempt_schemes(reason):
	"""被保険者でない場合の理由から、対象外とみなす制度の集合を返す（EMP-12）。"""
	return set(SI_EXEMPT_SCHEMES.get(reason or "", set()))


def get_display_name(employee, purpose="roster"):
	"""帳票に出す氏名（EMP-02）。

	- purpose="roster"（労働者名簿）：常に戸籍上の氏名（employee_name）
	- それ以外（給与明細等）：旧姓（通称）の表示を選んだ従業員は「旧姓＋名」、それ以外は戸籍上の氏名
	"""
	values = employee if isinstance(employee, dict) else frappe.db.get_value(
		"Employee", employee, ["employee_name", "first_name", "jp_former_name", "jp_display_former_name"], as_dict=True)
	if not values:
		return None
	if purpose != "roster" and values.get("jp_display_former_name") and values.get("jp_former_name"):
		return f"{values.get('jp_former_name')}　{values.get('first_name') or ''}".strip()
	return values.get("employee_name")


def set_salary_slip_display_name(doc, method=None):
	"""給与明細の表示氏名（EMP-02）。Salary Slip の validate から呼ぶ。"""
	if doc.employee and doc.meta.has_field("jp_display_name"):
		doc.jp_display_name = get_display_name(doc.employee, "payslip")


def notify_residence_expiry():
	"""日次：在留期間の満了日の N 日前（JP HR Settings、既定60日）に人事担当へ通知する（EMP-04）。"""
	from frappe.utils import add_days, today

	from japan_hr_compliance.jp_hr_core.contract import hr_users

	days = frappe.db.get_single_value("JP HR Settings", "residence_expiry_notice_days") or 60
	target = add_days(today(), days)
	rows = frappe.get_all("Employee", filters={"jp_residence_expiry": target, "status": ("!=", "Left")},
		fields=["name", "employee_name", "jp_residence_expiry"])
	recipients = hr_users()
	for e in rows:
		for user in recipients:
			frappe.get_doc({
				"doctype": "Notification Log", "for_user": user, "type": "Alert", "document_type": "Employee", "document_name": e.name,
				"subject": _("在留期間の満了日が近づいています：{0}（{1} 満了）").format(e.employee_name, e.jp_residence_expiry),
			}).insert(ignore_permissions=True)
	return [e.name for e in rows]


def validate_employee_number(doc):
	"""社員番号（標準の employee_number）を会社内で一意にする（EMP-03）。"""
	if not doc.employee_number:
		return
	doc.employee_number = doc.employee_number.strip()
	dup = frappe.db.get_value("Employee", {"company": doc.company, "employee_number": doc.employee_number, "name": ("!=", doc.name)})
	if dup:
		frappe.throw(_("社員番号 {0} は同じ会社の従業員 {1} に登録済みです。").format(doc.employee_number, dup), title=_("社員番号の重複"))


def validate_insurance(doc):
	"""保険番号の形式検証（EMP-10）と、資格取得日・喪失日の前後（EMP-11）。"""
	for fieldname, label, digits in INSURANCE_NUMBERS:
		if doc.get(fieldname):
			doc.set(fieldname, _throw_value_error(normalize.digits_only, doc.get(fieldname), label, digits))
	for acquired, lost, label in QUALIFICATION_DATES:
		if doc.get(acquired) and doc.get(lost) and getdate(doc.get(lost)) < getdate(doc.get(acquired)):
			frappe.throw(_("{0}の資格喪失日が資格取得日より前です。").format(label))


def validate_addresses(doc):
	"""住所履歴の正規化と、適用開始日の重複の検査（EMP-05・06）。行は適用開始日の順に並べる。"""
	seen = set()
	for row in doc.get("jp_addresses") or []:
		row.postal_code = _throw_value_error(normalize.format_postal_code, row.postal_code)
		row.lg_code = _throw_value_error(normalize.validate_lg_code, row.lg_code)
		key = str(getdate(row.valid_from))
		if key in seen:
			frappe.throw(_("住所の適用開始日 {0} が重複しています。").format(key))
		seen.add(key)
	(doc.get("jp_addresses") or []).sort(key=lambda r: getdate(r.valid_from))
	for i, row in enumerate(doc.get("jp_addresses") or [], 1):
		row.idx = i


def validate_bank_accounts(doc):
	"""振込口座の正規化と分割の規則（EMP-34）、口座振込の同意（EMP-35）。

	- 金融機関コード4桁・支店コード3桁・口座番号7桁（7桁未満は先頭をゼロで埋める）
	- 名義カナは全銀フォーマットの半角カナ
	- 振込順位は重複不可。定額のない口座（残額の振込先）はちょうど1件で、振込順位が最後
	- 給与の支払方法を口座振込（salary_mode = Bank）に変えるときは、口座振込の同意が必要
	"""
	rows = doc.get("jp_bank_accounts") or []
	for row in rows:
		row.bank_code = _throw_value_error(normalize.digits_only, row.bank_code, "金融機関コード", 4)
		row.branch_code = _throw_value_error(normalize.digits_only, row.branch_code, "支店コード", 3)
		account_no = _throw_value_error(normalize.digits_only, row.account_no, "口座番号")
		if account_no and len(account_no) > 7:
			frappe.throw(_("口座番号は7桁以内です（{0}）。").format(row.account_no))
		row.account_no = account_no.zfill(7) if account_no else account_no
		row.holder_kana = _throw_value_error(normalize.to_zengin_kana, row.holder_kana)
	if rows:
		priorities = [row.priority for row in rows]
		if len(set(priorities)) != len(priorities):
			frappe.throw(_("振込口座の振込順位が重複しています。"))
		remainder = [row for row in rows if not row.fixed_amount]
		if len(remainder) != 1:
			frappe.throw(_("振込額（定額）を空欄にした残額の振込先の口座を、ちょうど1件にしてください。"))
		if remainder[0].priority != max(priorities):
			frappe.throw(_("残額の振込先の口座は、振込順位を最後にしてください。"))
		rows.sort(key=lambda r: r.priority)
		for i, row in enumerate(rows, 1):
			row.idx = i

	before = doc.get_doc_before_save()
	if doc.salary_mode == "Bank" and (doc.is_new() or not before or before.salary_mode != "Bank"):
		from japan_hr_compliance.jp_hr_core.consent import BANK_TRANSFER, has_consent

		if not rows:
			frappe.throw(_("口座振込にするには振込口座を登録してください。"))
		if doc.is_new() or not has_consent(doc.name, BANK_TRANSFER):
			frappe.throw(_("口座振込の同意の記録がないため、給与の支払方法を口座振込にできません（労基則7条の2）。"),
				title=_("口座振込の同意がありません"))


def validate_dependents(doc):
	"""扶養親族の正規化（EMP-40・42）と、年分ごとの所得見積の対応（EMP-41）。"""
	names = set()
	for row in doc.get("jp_dependents") or []:
		row.dependent_name = (row.dependent_name or "").strip()
		row.dependent_kana = _throw_value_error(normalize.to_zenkaku_kana, row.dependent_kana, "扶養親族の氏名カナ")
		if row.hi_dependent_from and row.hi_dependent_to and getdate(row.hi_dependent_to) < getdate(row.hi_dependent_from):
			frappe.throw(_("{0} の被扶養者の解除日が認定日より前です。").format(row.dependent_name))
		names.add(row.dependent_name)
	seen = set()
	for row in doc.get("jp_dependent_incomes") or []:
		row.dependent_name = (row.dependent_name or "").strip()
		if row.dependent_name not in names:
			frappe.throw(_("所得見積の行の氏名 {0} が扶養親族の表にありません。").format(row.dependent_name))
		key = (row.dependent_name, row.tax_year)
		if key in seen:
			frappe.throw(_("{0} の {1} 年分の所得見積が重複しています。").format(*key))
		seen.add(key)


def validate_separation(doc):
	"""名簿用の離職区分と、区分ごとの必須入力（EMP-44）。"""
	if not doc.relieving_date and doc.status != "Left":
		return
	if not doc.jp_separation_type:
		frappe.throw(_("退職者は離職区分（名簿用）を選んでください。"), title=_("離職区分がありません"))
	if doc.jp_separation_type == "解雇" and not (doc.jp_dismissal_reason or "").strip():
		frappe.throw(_("解雇の場合は解雇の理由を入力してください（労基則53条1項5号）。"))
	if doc.jp_separation_type == "死亡":
		if not doc.jp_death_date or not (doc.jp_death_cause or "").strip():
			frappe.throw(_("死亡の場合は死亡の年月日と原因を入力してください（労基則53条1項6号）。"))


def validate_retained_rows(doc):
	"""退職者の扶養親族の行は削除させない（EMP-47）。"""
	before = doc.get_doc_before_save()
	if not before or not (before.status == "Left" or before.relieving_date):
		return
	current = {row.name for row in doc.get("jp_dependents") or []}
	removed = [row.dependent_name for row in before.get("jp_dependents") or [] if row.name not in current]
	if removed:
		frappe.throw(_("退職者の扶養親族の記録は削除できません：{0}").format("、".join(removed)))


def validate_contract_period(doc):
	"""退職日より後に始まる確定済みの契約がないこと（EMP-20）。"""
	if doc.is_new() or not doc.relieving_date:
		return
	later = frappe.db.get_value("JP Employment Contract",
		{"employee": doc.name, "docstatus": 1, "valid_from": (">", doc.relieving_date)}, "name")
	if later:
		frappe.throw(_("退職日より後に始まる雇用契約 {0} があります。先に取り消してください。").format(later))


def on_update(doc, method=None):
	before = doc.get_doc_before_save()
	if not before or str(before.relieving_date or "") != str(doc.relieving_date or ""):
		from japan_hr_compliance.jp_hr_core.contract import refresh_valid_to
		from japan_hr_compliance.jp_hr_core.my_number import refresh_disposal_for_employee

		refresh_valid_to(doc.name)
		refresh_disposal_for_employee(doc.name)
	if doc.flags.jp_contract_sync:
		return
	from japan_hr_compliance.jp_hr_core import dependents, procedures

	procedures.on_employee_update(doc, before)
	dependents.record_dependent_changes(doc, before)


def on_trash(doc, method=None):
	"""退職者の Employee は管理者でも削除できない（EMP-47。名簿の保存期限まで保持）。"""
	if doc.status == "Left" or doc.relieving_date:
		frappe.throw(_("退職者の従業員は削除できません（労働者名簿の保存のため。労基法109条）。"), frappe.PermissionError)
	# 誤登録の削除：自動で作った未完了の手続きタスクは一緒に消す（完了したタスクがあれば削除できない）
	for name in frappe.get_all("JP Procedure Task", filters={"employee": doc.name, "status": ("!=", "完了")}, pluck="name"):
		frappe.delete_doc("JP Procedure Task", name, ignore_permissions=True)
