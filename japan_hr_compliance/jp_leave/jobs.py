"""日次処理（LVR-16・20・30・32・34・53）。

1. 失効（最終使用日の翌日）と退職による消滅
2. 付与予定の作成（基準日の N 日前）と基準日の自動確定。確認待ち・未確定のまま基準日を過ぎた付与予定は
   付与漏れとして毎日通知する
3. 年5日の取得日数の更新と通知
4. hrms の残高と付与単位の残日数の照合

各処理は ``as_of``（既定は今日）を受け取る。テストでは日付を指定して呼ぶ。
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate

from japan_hr_compliance.jp_leave import container, five_day_service, grant_service
from japan_hr_compliance.jp_leave.utils import (
	GRANT,
	ST_ACTIVE,
	ST_EXPIRED,
	ST_EXTINGUISHED,
	ST_NO_GRANT,
	ST_PENDING,
	ST_PLANNED,
	D,
	fl,
	get_settings,
	hr_recipients,
	notify,
	today_,
)

LOOKBACK_DAYS = 30  # 処理が止まっていた場合に、過去の基準日の付与予定を作る範囲


def daily(as_of=None, employees=None):
	"""日次処理。employees（Employee の dict の一覧）を渡すと付与予定の作成・確定をその従業員に限る（テスト用）。"""
	as_of = getdate(as_of or today_())
	steps = (process_lapse, process_retirement, lambda d: create_plans(d, employees),
		lambda d: confirm_due_plans(d, [e.name for e in employees] if employees else None), check_incomplete_rounds,
		five_day_service.update_all, five_day_service.send_notices, reconcile)
	for step in steps:
		try:
			step(as_of)
		except Exception:
			frappe.log_error(title=f"JP Leave daily: {getattr(step, '__name__', 'step')}")
			if frappe.flags.in_test:
				raise


# ------------------------------------------------------------------ 失効・消滅
def process_lapse(as_of=None):
	"""最終使用日を過ぎた付与分を失効させ、残日数を器の負の行にする（LVR-30）。"""
	as_of = getdate(as_of or today_())
	done = []
	for g in frappe.get_all(GRANT, filters={"docstatus": 1, "status": ST_ACTIVE, "expiry_date": ("<", as_of)},
			fields=["name"], order_by="expiry_date asc"):
		grant = frappe.get_doc(GRANT, g.name)
		remaining = D(grant.remaining_days)
		entry = container.add_negative(grant, fl(remaining), add_days(grant.expiry_date, 1)) if remaining > 0 else None
		grant.db_set({"status": ST_EXPIRED, "lapsed_days": fl(D(grant.lapsed_days) + remaining), "remaining_days": 0,
			"lapse_ledger_entry": entry}, update_modified=False)
		done.append(grant.name)
	return done


def process_retirement(as_of=None):
	"""退職日を過ぎた従業員の残日数を「退職による消滅」とし、器を退職日で締める（LVR-34）。"""
	as_of = getdate(as_of or today_())
	done = []
	rows = frappe.db.sql(
		"""select g.name, e.relieving_date from `tabJP Annual Leave Grant` g
		join `tabEmployee` e on e.name = g.employee
		where g.docstatus=1 and g.status=%s and e.relieving_date is not null and e.relieving_date < %s""",
		(ST_ACTIVE, as_of), as_dict=True,
	)
	for r in rows:
		grant = frappe.get_doc(GRANT, r.name)
		remaining = D(grant.remaining_days)
		entry = container.add_negative(grant, fl(remaining), add_days(r.relieving_date, 1)) if remaining > 0 else None
		grant.db_set({"status": ST_EXTINGUISHED, "lapsed_days": fl(D(grant.lapsed_days) + remaining), "remaining_days": 0,
			"lapse_ledger_entry": entry}, update_modified=False)
		grant.add_comment("Info", _("退職日 {0} により残日数 {1} 日が消滅しました（LVR-34）。").format(r.relieving_date, fl(remaining)))
		container.close_container(grant.employee, grant.leave_type, r.relieving_date)
		done.append(grant.name)
	return done


# ------------------------------------------------------------------ 付与予定
def active_employees(as_of):
	return frappe.get_all(
		"Employee",
		filters={"status": ("!=", "Left"), "date_of_joining": ("is", "set")},
		or_filters=[["relieving_date", "is", "not set"], ["relieving_date", ">=", as_of]],
		fields=["name", "company", "date_of_joining"],
	)


def create_plans(as_of=None, employees=None):
	"""基準日の N 日前（会社設定、既定7日）に付与予定を作る（LVR-20）。"""
	as_of = getdate(as_of or today_())
	created = []
	for emp in employees or active_employees(as_of):
		settings = get_settings(emp.company)
		window_end = add_days(as_of, int(settings.plan_days_before or 0))
		window_start = add_days(as_of, -LOOKBACK_DAYS)
		try:
			created += _plans_for(emp, settings, window_start, window_end)
		except Exception:
			frappe.log_error(title=f"JP Leave plan: {emp.name}")
			if frappe.flags.in_test:
				raise
	return created


def _plans_for(emp, settings, start, end):
	created = []
	hire = getdate(emp.date_of_joining)
	has_grant = frappe.db.exists(GRANT, {"employee": emp.name, "docstatus": 1, "grant_kind": ("!=", "調整")})
	if settings.grant_method == "斉一的付与" and has_grant:
		for d in grant_service.uniform_dates(settings, start, end):
			if frappe.db.exists(GRANT, {"employee": emp.name, "grant_date": d, "docstatus": ("<", 2)}):
				continue
			values = grant_service.build_uniform_plan(emp.name, d, settings)
			created.append(_insert_plan(values))
		return created
	for ordinal, base in grant_service.individual_base_dates(hire, start, end):
		if frappe.db.exists(GRANT, {"employee": emp.name, "docstatus": ("<", 2), "grant_kind": ("!=", "調整"),
				"round_no": ordinal}):
			continue
		values = grant_service.build_individual_plan(emp.name, base)
		if settings.grant_method == "斉一的付与":
			values["status"] = ST_PENDING
			values["pending_reasons"] = "\n".join(filter(None, [values.get("pending_reasons"),
				_("斉一的付与の会社です。初回の付与（入社時の前倒し等）を入力画面で登録してください。登録がなければ法定の基準日のこの予定で付与します。")]))
		created.append(_insert_plan(values))
	return created


def _insert_plan(values):
	doc = frappe.get_doc(dict(doctype=GRANT, **values))
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def refresh_plan(doc):
	"""付与予定を最新の勤怠・契約で作り直す（下書きの間だけ）。"""
	settings = get_settings(doc.company)
	is_individual = doc.plan_method != "全社基準日"
	if not is_individual:
		values = grant_service.build_uniform_plan(doc.employee, doc.grant_date, settings, existing_calc=doc.attendance_calc)
	else:
		values = grant_service.build_individual_plan(doc.employee, doc.grant_date, existing_calc=doc.attendance_calc)
		if settings.grant_method == "斉一的付与" and values["status"] == ST_PLANNED and not frappe.db.exists(
				GRANT, {"employee": doc.employee, "docstatus": 1, "grant_kind": ("!=", "調整")}):
			values["status"] = ST_PENDING
			values["pending_reasons"] = doc.pending_reasons
	doc.update(values)
	doc.flags.ignore_permissions = True
	doc.save()
	return doc


def confirm_due_plans(as_of=None, employees=None):
	"""基準日を迎えた付与予定を確定する。確認待ちのまま基準日を過ぎたものは付与漏れとして毎日通知する。"""
	as_of = getdate(as_of or today_())
	confirmed, overdue = [], []
	filters = {"docstatus": 0, "is_manual": 0, "grant_date": ("<=", as_of),
		"status": ("in", (ST_PLANNED, ST_PENDING, ST_NO_GRANT))}
	if employees:
		filters["employee"] = ("in", employees)
	for row in frappe.get_all(GRANT, filters=filters, pluck="name", order_by="grant_date asc"):
		doc = refresh_plan(frappe.get_doc(GRANT, row))
		settings = get_settings(doc.company)
		auto = settings.confirm_method == "自動"
		if doc.status == ST_PLANNED and grant_service.needs_manual_check(doc) and not doc.manual_checked:
			doc.db_set({"status": ST_PENDING, "pending_reasons": _("前倒し付与です。システムで判定できない事項（就業規則の定め等）を確認して確定してください（LVR-22）。")},
				update_modified=False)
		if doc.status == ST_NO_GRANT or (doc.status == ST_PLANNED and auto and doc.judgement_ok
				and not grant_service.needs_manual_check(doc)):
			try:
				doc.submit()
				confirmed.append(doc.name)
				continue
			except frappe.ValidationError as e:
				frappe.clear_last_message()
				doc.reload()
				doc.db_set({"status": ST_PENDING, "pending_reasons": str(e)}, update_modified=False)
		if getdate(doc.grant_date) < as_of:
			overdue.append(doc.name)
			notify(hr_recipients(), _("【年休の付与漏れ】{0}：基準日 {1} の年休付与が未確定です（{2}）").format(
				doc.employee_name, doc.grant_date, (doc.pending_reasons or _("担当者の確定待ち")).replace("\n", "、")),
				GRANT, doc.name)
	return confirmed, overdue


def check_incomplete_rounds(as_of=None):
	"""分割付与で法定の基準日を過ぎても法定日数に達していない回を通知する（LVR-22 ③）。"""
	as_of = getdate(as_of or today_())
	rows = frappe.db.sql(
		"""select employee, employee_name, round_no, max(legal_base_date) legal_base_date, max(legal_days) legal_days,
			sum(days) total
		from `tabJP Annual Leave Grant`
		where docstatus=1 and status in %s and grant_kind != '調整' and round_no is not null
		group by employee, employee_name, round_no
		having max(legal_base_date) < %s and sum(days) < max(legal_days)""",
		((ST_ACTIVE, ST_EXPIRED, ST_EXTINGUISHED), as_of), as_dict=True,
	)
	result = []
	for r in rows:
		later = frappe.db.exists(GRANT, {"employee": r.employee, "docstatus": 1, "round_no": (">", r.round_no)})
		if later:
			continue
		result.append(r)
		notify(hr_recipients(), _("【年休の付与不足】{0}：第{1}回の付与日数の累計 {2} 日が法定の基準日 {3} までに法定の {4} 日に達していません").format(
			r.employee_name, r.round_no, r.total, r.legal_base_date, r.legal_days), "Employee", r.employee)
	return result


# ------------------------------------------------------------------ 照合
def reconcile(as_of=None):
	"""hrms の残高と年休付与の残日数の合計を照合し、差異を担当者に通知する（LVR-32）。"""
	as_of = getdate(as_of or today_())
	diffs = []
	for alloc in frappe.get_all("Leave Allocation", filters={"docstatus": 1, "jp_leave_container": 1},
			fields=["name", "employee", "employee_name", "leave_type"]):
		hrms = container.hrms_balance(alloc.employee, alloc.leave_type, as_of, for_consumption=True)
		ours = fl(frappe.db.sql(
			"""select coalesce(sum(remaining_days), 0) from `tabJP Annual Leave Grant`
			where employee=%s and leave_type=%s and docstatus=1 and status=%s and grant_date <= %s""",
			(alloc.employee, alloc.leave_type, ST_ACTIVE, as_of),
		)[0][0])
		if abs(hrms - ours) > 0.001:
			diffs.append(frappe._dict(employee=alloc.employee, hrms=hrms, grants=ours))
			notify(hr_recipients(), _("【年休の残高の差異】{0}：hrms の残高 {1} 日、年休付与の残日数の合計 {2} 日").format(
				alloc.employee_name, hrms, ours), "Leave Allocation", alloc.name)
	return diffs

