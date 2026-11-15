"""入退社・扶養異動の手続きタスク（従業員マスタ要件 B-1 v1.0 EMP-80〜82、2.2）。

期限は制度ごとの規則（JP Procedure Rule）で、基準日と数え方を別々に持つ（社保と雇保で共通化しない）。

- 社保の資格喪失届：資格喪失日（退職日の翌日）を含めて5日以内 → 3/31 退職で 4/5
- 雇保の資格喪失届：被保険者でなくなった日（退職日の翌日）の翌日から起算して10日以内 → 3/31 退職で 4/11
- 雇保の資格取得届：資格取得日の属する月の翌月10日まで
- 退職者への源泉徴収票：退職の日以後1か月以内
- 住民税の給与所得者異動届：仮の設定（翌月10日）。規則マスタで変更できる

休日による期限の繰り延べは扱わない（要件書に定めがないため。必要なら規則で調整する）。
"""

import calendar

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, get_first_day, getdate

from japan_hr_compliance.jp_hr_core.dependents import age_on

RULE = "JP Procedure Rule"
TASK = "JP Procedure Task"

HIRE, LEAVE, DEPENDENT = "入社", "退職", "扶養異動"

INCLUSIVE = "基準日を含めてN日以内"
FROM_NEXT_DAY = "基準日の翌日から起算してN日以内"
NEXT_MONTH_DAY = "基準日の属する月の翌月D日まで"
MONTHS = "基準日からNか月以内"
NO_DUE = "期限なし"

SLIP_AGE = 59  # 離職日に59歳以上は交付希望にかかわらず離職証明書を作成する（4.11）

# 初期の期限規則。マスタに登録し、会社が変更できる（既存の規則は上書きしない）
DEFAULT_RULES = (
	dict(rule_code="SI-ACQ", procedure_name="健康保険・厚生年金保険 被保険者資格取得届", event=HIRE, scheme="健康保険・厚生年金",
		condition="なし", base_date="資格取得日（健保・厚年）", due_method=INCLUSIVE, due_value=5,
		due_text="資格取得日（入社日）を含めて5日以内", legal_basis="健保則24条、厚年則15条"),
	dict(rule_code="EI-ACQ", procedure_name="雇用保険 被保険者資格取得届", event=HIRE, scheme="雇用保険",
		condition="なし", base_date="資格取得日（雇保）", due_method=NEXT_MONTH_DAY, due_value=10,
		due_text="資格取得日の属する月の翌月10日まで", legal_basis="雇保則6条"),
	dict(rule_code="SI-LOSS", procedure_name="健康保険・厚生年金保険 被保険者資格喪失届", event=LEAVE, scheme="健康保険・厚生年金",
		condition="社会保険の被保険者", base_date="資格喪失日（健保・厚年）", due_method=INCLUSIVE, due_value=5,
		due_text="退職日の翌日（資格喪失日）を含めて5日以内", legal_basis="健保則29条、厚年則22条"),
	dict(rule_code="EI-LOSS", procedure_name="雇用保険 被保険者資格喪失届", event=LEAVE, scheme="雇用保険",
		condition="雇用保険の被保険者", base_date="被保険者でなくなった日（雇保）", due_method=FROM_NEXT_DAY, due_value=10,
		due_text="被保険者でなくなった日（退職日の翌日）の翌日から10日以内", legal_basis="雇保則7条"),
	dict(rule_code="EI-SLIP", procedure_name="雇用保険 離職証明書（離職票の交付）", event=LEAVE, scheme="雇用保険",
		condition="離職証明書（交付希望または離職日に59歳以上）", base_date="被保険者でなくなった日（雇保）", due_method=FROM_NEXT_DAY,
		due_value=10, due_text="資格喪失届に添付（被保険者でなくなった日の翌日から10日以内）", legal_basis="雇保則7条"),
	dict(rule_code="WH-SLIP", procedure_name="退職者への給与所得の源泉徴収票の交付", event=LEAVE, scheme="所得税",
		condition="なし", base_date="退職日", due_method=MONTHS, due_value=1,
		due_text="退職の日以後1か月以内", legal_basis="所法226条1項"),
	dict(rule_code="RT-CHANGE", procedure_name="住民税 給与所得者異動届出書（{resident_tax}）", event=LEAVE, scheme="住民税",
		condition="なし", base_date="退職日", due_method=NEXT_MONTH_DAY, due_value=10,
		due_text="異動の翌月10日まで（仮の設定）", legal_basis="地方税法321条の5第3項（仮の設定）", provisional=1),
	dict(rule_code="RT-LUMPSUM", procedure_name="住民税 残税額の一括徴収（最終の給与・退職手当から控除）", event=LEAVE, scheme="住民税",
		condition="住民税：一括徴収", base_date="退職日", due_method=INCLUSIVE, due_value=1,
		due_text="最終の給与・退職手当の支払時（退職日を目安）", legal_basis="地方税法321条の5第2項（要確認）", provisional=1),
	dict(rule_code="DEP-HI", procedure_name="健康保険 被扶養者（異動）届・国民年金第3号被保険者関係届", event=DEPENDENT,
		scheme="健康保険・厚生年金", condition="被扶養者の異動", base_date="異動日", due_method=INCLUSIVE, due_value=5,
		due_text="異動日（事実発生日）を含めて5日以内", legal_basis="健保則38条（要確認）", provisional=1),
	dict(rule_code="DEP-TAX", procedure_name="給与所得者の扶養控除等（異動）申告書の受理と源泉の扶養人数の再計算", event=DEPENDENT,
		scheme="所得税", condition="税の扶養の異動", base_date="異動日", due_method=NO_DUE, due_value=0,
		due_text="異動後、最初の給与の支払日の前日まで", legal_basis="所法194条2項"),
)


def ensure_rules():
	for rule in DEFAULT_RULES:
		if not frappe.db.exists(RULE, rule["rule_code"]):
			frappe.get_doc({"doctype": RULE, "enabled": 1, **rule}).insert(ignore_permissions=True)


def compute_due(base, method, value):
	"""基準日と数え方から期限を返す。期限なしは None。"""
	if not base or method == NO_DUE:
		return None
	base = getdate(base)
	value = cint(value)
	if method == INCLUSIVE:
		return add_days(base, value - 1)
	if method == FROM_NEXT_DAY:
		return add_days(base, value)
	if method == NEXT_MONTH_DAY:
		first = getdate(get_first_day(add_months(base, 1)))
		last = calendar.monthrange(first.year, first.month)[1]
		return first.replace(day=max(1, min(value, last)))
	if method == MONTHS:
		return getdate(add_months(base, value))
	frappe.throw(_("期限の数え方 {0} が不明です。").format(method))


# --- 保険の加入・除外の判定（タスクを作るかどうかだけに使う。保険料の判定は B-5） --------------------------
def is_si_member(emp):
	return bool(emp.jp_hi_acquired or emp.jp_pi_acquired or emp.jp_basic_pension_no or emp.jp_hi_number)


def is_ei_member(emp):
	return bool(emp.jp_ei_acquired or emp.jp_emp_ins_no)


def excluded(emp, scheme):
	from japan_hr_compliance.jp_hr_core.employee import exempt_schemes

	schemes = exempt_schemes(emp.jp_si_exempt_reason)
	if scheme == "健康保険・厚生年金":
		return {"健康保険", "厚生年金"} <= schemes
	if scheme == "雇用保険":
		return "雇用保険" in schemes
	return False


def needs_separation_slip(emp):
	"""離職証明書が必要か：交付希望あり、または離職日（退職日）現在59歳以上（EMP-81）。"""
	if cint(emp.jp_separation_slip_requested):
		return True
	return bool(emp.date_of_birth and emp.relieving_date and age_on(emp.date_of_birth, emp.relieving_date) >= SLIP_AGE)


def condition_met(rule, emp, context):
	cond = rule.condition or "なし"
	if cond == "社会保険の被保険者":
		return is_si_member(emp)
	if cond == "雇用保険の被保険者":
		return is_ei_member(emp)
	if cond.startswith("離職証明書"):
		return is_ei_member(emp) and needs_separation_slip(emp)
	if cond == "住民税：一括徴収":
		return emp.jp_resident_tax_on_leave == "一括徴収"
	if cond == "被扶養者の異動":
		return bool(context.get("hi_related"))
	if cond == "税の扶養の異動":
		return bool(context.get("tax_related"))
	return True


def base_date_for(rule, emp, context):
	relieving = getdate(emp.relieving_date) if emp.relieving_date else None
	joining = getdate(emp.date_of_joining) if emp.date_of_joining else None
	b = rule.base_date
	if b == "入社日":
		return joining
	if b == "退職日":
		return relieving
	if b == "資格取得日（健保・厚年）":
		return getdate(emp.jp_hi_acquired or emp.jp_pi_acquired or joining) if (emp.jp_hi_acquired or emp.jp_pi_acquired or joining) else None
	if b == "資格取得日（雇保）":
		return getdate(emp.jp_ei_acquired or joining) if (emp.jp_ei_acquired or joining) else None
	if b == "資格喪失日（健保・厚年）":
		lost = emp.jp_hi_lost or emp.jp_pi_lost
		return getdate(lost) if lost else (add_days(relieving, 1) if relieving else None)
	if b == "被保険者でなくなった日（雇保）":
		return getdate(emp.jp_ei_lost) if emp.jp_ei_lost else (add_days(relieving, 1) if relieving else None)
	if b == "異動日":
		return getdate(context.get("change_date")) if context.get("change_date") else None
	return None


def procedure_label(rule, emp):
	return rule.procedure_name.replace("{resident_tax}", emp.jp_resident_tax_on_leave or "徴収区分は未設定")


def create_tasks(employee, event, context=None, reference=None):
	"""契機（入社・退職・扶養異動）の手続きタスクを作る。同じ規則・同じ契機のタスクは重複して作らない。

	退職日の変更などで基準日が変わった場合は、未着手のタスクの期限を更新する。条件を満たさなくなった
	未着手のタスクは「対象外」にする。

	Returns:
		list[str]: 作成・更新したタスクの name
	"""
	if frappe.db.get_single_value("JP HR Settings", "disable_procedure_tasks"):
		return []
	context = context or {}
	emp = frappe.get_doc("Employee", employee)
	scope = context.get("scope") or event
	changed = []
	for rule in frappe.get_all(RULE, filters={"event": event, "enabled": 1}, fields=["*"], order_by="rule_code asc"):
		key = f"{employee}|{scope}|{rule.name}"
		existing = frappe.db.get_value(TASK, {"dedupe_key": key}, ["name", "status", "due_date"], as_dict=True)
		if event != DEPENDENT and (excluded(emp, rule.scheme) or not condition_met(rule, emp, context)):
			if existing and existing.status == "未着手":
				frappe.db.set_value(TASK, existing.name, {"status": "対象外", "remarks": _("作成の条件を満たさなくなったため対象外")})
				changed.append(existing.name)
			continue
		if event == DEPENDENT and not condition_met(rule, emp, context):
			continue
		base = base_date_for(rule, emp, context)
		if not base:
			continue
		due = compute_due(base, rule.due_method, rule.due_value)
		values = {"procedure_name": procedure_label(rule, emp), "base_date": base, "due_date": due, "due_text": rule.due_text,
			"provisional": cint(rule.provisional), "legal_basis": rule.legal_basis}
		if existing:
			if existing.status == "未着手":
				frappe.db.set_value(TASK, existing.name, values)
				changed.append(existing.name)
			continue
		task = frappe.get_doc({"doctype": TASK, "employee": employee, "event": event, "rule": rule.name, "status": "未着手",
			"dedupe_key": key, **values})
		if reference:
			task.reference_doctype, task.reference_name = reference
		task.insert(ignore_permissions=True)
		changed.append(task.name)
	return changed


def on_employee_update(doc, before):
	"""Employee の保存から入社・退職の手続きタスクを作る。"""
	if before is None:
		create_tasks(doc.name, HIRE)
		if doc.relieving_date:
			create_tasks(doc.name, LEAVE)
		return
	watched = ("relieving_date", "jp_separation_slip_requested", "jp_resident_tax_on_leave", "jp_si_exempt_reason", "jp_hi_lost",
		"jp_pi_lost", "jp_ei_lost", "date_of_birth")
	if doc.relieving_date and any(str(before.get(f) or "") != str(doc.get(f) or "") for f in watched):
		create_tasks(doc.name, LEAVE)
	elif before.relieving_date and not doc.relieving_date:
		# 退職の取消：未着手の退職手続きを対象外にする
		for name in frappe.get_all(TASK, filters={"employee": doc.name, "event": LEAVE, "status": "未着手"}, pluck="name"):
			frappe.db.set_value(TASK, name, {"status": "対象外", "remarks": _("退職日が取り消されたため対象外")})


def get_resident_tax_on_leave(employee):
	"""退職時の住民税の徴収区分（EMP-82）。住民税（B-7）への引き渡し用。"""
	values = frappe.db.get_value("Employee", employee, ["relieving_date", "jp_resident_tax_on_leave"], as_dict=True)
	return frappe._dict(relieving_date=values.relieving_date, collection=values.jp_resident_tax_on_leave) if values else None
