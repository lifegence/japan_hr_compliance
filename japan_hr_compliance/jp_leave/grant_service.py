"""年休付与の予定の作成・前倒し／分割付与の判定（LVR-04〜08・10〜16・20〜23）。

純粋な計算は jp_leave.calc（plan_individual_grant・judge_advance_grants など）を呼び、ここでは
従業員・雇用契約（B-1 の get_contract_at）・勤怠（hrms の Attendance と Holiday List）を集めて渡す。
"""

from datetime import timedelta
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import getdate

from japan_hr_compliance.jp_hr_core.contract import get_contract_at
from japan_hr_compliance.jp_leave import attendance_source
from japan_hr_compliance.jp_leave.calc.advance import (
	MANUAL_CHECK_ITEMS,
	GrantInput,
	GrantKind,
	judge_advance_grants,
	legal_attendance_period,
)
from japan_hr_compliance.jp_leave.calc.attendance import attendance_period
from japan_hr_compliance.jp_leave.calc.category import (
	AnnualDaysBasis,
	ContractTerms,
	MissingContractData,
	default_annual_days_from_actual,
	determine_category,
	select_grant_table,
)
from japan_hr_compliance.jp_leave.calc.dates import base_date_ordinal, nth_base_date, service_months
from japan_hr_compliance.jp_leave.calc.five_day import is_obligation_target
from japan_hr_compliance.jp_leave.calc.grant import PlanStatus, plan_individual_grant
from japan_hr_compliance.jp_leave.calc.grant_table import LEGAL_GRANT_TABLE, WorkCategory, legal_days
from japan_hr_compliance.jp_leave.utils import (
	GRANT,
	GRANT_TABLE,
	ST_ACTIVE,
	ST_EXPIRED,
	ST_EXTINGUISHED,
	ST_NO_GRANT,
	ST_PENDING,
	ST_PLANNED,
	D,
	fl,
)

BASIS_MAP = {"確定": AnnualDaysBasis.CALENDAR, "目安": AnnualDaysBasis.ESTIMATE}
COUNTED_STATES = (ST_ACTIVE, ST_EXPIRED, ST_EXTINGUISHED, ST_NO_GRANT)
SPLIT_SHORT_MARK = "に達していません"


# ------------------------------------------------------------------ 従業員・契約
def employee_info(employee):
	emp = frappe.db.get_value(
		"Employee", employee,
		["name", "company", "date_of_joining", "relieving_date", "status", "jp_service_start_date",
			"jp_service_start_confirmed"],
		as_dict=True,
	)
	if not emp:
		frappe.throw(_("従業員 {0} が見つかりません。").format(employee))
	emp.hire_date = getdate(emp.date_of_joining)
	emp.service_start = getdate(emp.jp_service_start_date) if emp.jp_service_start_date else emp.hire_date
	emp.service_confirmed = bool(emp.jp_service_start_confirmed) if emp.jp_service_start_confirmed is not None else True
	return emp


def contract_terms(contract):
	"""B-1 の契約（get_contract_at の結果）を calc の ContractTerms に詰める。"""
	if not contract:
		return None
	weekly_days = contract.weekly_days
	if weekly_days:
		weekly_days = int(weekly_days) if float(weekly_days).is_integer() else weekly_days
	return ContractTerms(
		weekly_hours=D(contract.weekly_hours) if contract.weekly_hours else None,
		weekly_days=weekly_days or None,
		annual_days=contract.annual_days or None,
		annual_days_basis=BASIS_MAP.get(contract.annual_days_basis, AnnualDaysBasis.CALENDAR),
		work_rule=contract.work_rule_class or None,
	)


def grant_tables(company):
	from japan_hr_compliance.jp_leave.doctype.jp_leave_grant_table.jp_leave_grant_table import get_tables

	return get_tables(company)


def table_doc_name(table):
	return table.name if table is not LEGAL_GRANT_TABLE and frappe.db.exists(GRANT_TABLE, table.name) else (
		frappe.db.get_value(GRANT_TABLE, {"is_legal_default": 1}, "name")
	)


def actual_annual_days(employee, terms, period, is_initial):
	"""週所定労働日数が定まらない者の実績による年間所定労働日数（LVR-08）。"""
	if terms is None or terms.weekly_days is not None:
		return None
	worked = attendance_source.worked_days_count(employee, period[0], period[1])
	return default_annual_days_from_actual(worked, is_initial)


def decide_category(terms, actual):
	"""区分の判定。判定できなければ (None, 理由)。"""
	if terms is None:
		return None, "基準日時点の雇用契約がありません"
	try:
		return determine_category(terms, actual), None
	except (MissingContractData, ValueError, KeyError) as e:
		return None, str(e)


# ------------------------------------------------------------------ 個別基準日の付与予定
def individual_base_dates(hire_date, start, end):
	"""[start, end] に入る個別基準日の (回, 基準日)。"""
	result = []
	n = 1
	while True:
		d = nth_base_date(hire_date, n)
		if d > end:
			return result
		if d >= start:
			result.append((n, d))
		n += 1


def build_individual_plan(employee, base_date, existing_calc=None):
	"""個別基準日の付与予定（年休付与の項目の dict）と算定明細を作る（LVR-20）。"""
	emp = employee_info(employee)
	base_date = getdate(base_date)
	ordinal = base_date_ordinal(emp.hire_date, base_date)
	prev = nth_base_date(emp.hire_date, ordinal - 1) if ordinal > 1 else None
	period = attendance_period(emp.hire_date, base_date, prev)
	reasons = []

	attendance = None
	try:
		attendance = attendance_source.compute(employee, period[0], period[1])
	except attendance_source.NoCalendar as e:
		reasons.append(str(e))
	contract = get_contract_at(employee, base_date)
	terms = contract_terms(contract)
	actual = actual_annual_days(employee, terms, period, ordinal == 1)
	tables = grant_tables(emp.company)
	try:
		plan = plan_individual_grant(emp.hire_date, base_date, terms, attendance, tables, emp.service_start, actual,
			emp.service_confirmed)
	except (ValueError, KeyError) as e:  # 週所定労働日数が整数でない等
		plan = None
		reasons.append(str(e))

	calc_doc = attendance_source.save_calc(employee, attendance, name=existing_calc) if attendance else None
	values = {
		"employee": employee, "company": emp.company, "grant_date": base_date, "round_no": ordinal, "is_manual": 0,
		"plan_method": "個別基準日",
		"hire_date": emp.hire_date, "service_start_date": emp.service_start,
		"service_months": service_months(emp.service_start, base_date),
		"attendance_calc": calc_doc.name if calc_doc else None,
		"attendance_rate": calc_doc.rate if calc_doc else None,
	}
	values.update(contract_values(contract))
	if plan is not None:
		reasons = reasons + plan.reasons if plan.status != PlanStatus.NO_GRANT else reasons
		values.update({
			"days": fl(plan.days),
			"category": plan.category.value if plan.category else None,
			"category_basis": plan.category_decision.basis if plan.category_decision else None,
			"annual_days_basis": plan.category_decision.annual_days_basis.value
			if plan.category_decision and plan.category_decision.annual_days_basis else None,
			"table_days": fl(plan.days),
		})
		if plan.category_decision and plan.category_decision.annual_days_used is not None:
			values["annual_days"] = plan.category_decision.annual_days_used
		if plan.table_name:
			table = tables.get(terms.work_rule) if terms and terms.work_rule in tables else LEGAL_GRANT_TABLE
			values["grant_table"] = table_doc_name(table)
		status = {PlanStatus.PLANNED: ST_PLANNED, PlanStatus.PENDING: ST_PENDING, PlanStatus.NO_GRANT: ST_NO_GRANT}[plan.status]
		if plan.status == PlanStatus.NO_GRANT:
			values["pending_reasons"] = "\n".join(plan.reasons)
	else:
		status = ST_PENDING
	if reasons and status != ST_NO_GRANT:
		status = ST_PENDING
		values["pending_reasons"] = "\n".join(dict.fromkeys(reasons))
	elif status == ST_PLANNED:
		values["pending_reasons"] = None
	values["status"] = status
	return values


def contract_values(contract):
	if not contract:
		return {"contract": None, "work_rule_class": None, "weekly_days": None, "weekly_hours": None}
	return {
		"contract": contract.name, "work_rule_class": contract.work_rule_class, "weekly_days": contract.weekly_days,
		"weekly_hours": contract.weekly_hours, "annual_days": contract.annual_days,
	}


# ------------------------------------------------------------------ 斉一的付与の付与予定
def uniform_dates(settings, start, end):
	"""[start, end] に入る全社基準日。"""
	result = []
	for year in range(start.year, end.year + 1):
		for month, day in settings.uniform_base_dates:
			try:
				d = getdate(f"{year:04d}-{int(month):02d}-{int(day):02d}")
			except Exception:
				continue
			if start <= d <= end:
				result.append(d)
	return sorted(result)


def confirmed_rounds(employee, exclude=None):
	"""確定済み（付与なしを含む）の付与を回ごとにまとめる（調整を除く）。"""
	filters = {"employee": employee, "docstatus": 1, "status": ("in", COUNTED_STATES), "grant_kind": ("!=", "調整")}
	rows = frappe.get_all(GRANT, filters=filters, fields=["name", "grant_date", "days", "round_no", "status", "legal_days"],
		order_by="grant_date asc, creation asc")
	rounds = {}
	for r in rows:
		if r.name == exclude:
			continue
		rounds.setdefault(r.round_no or 0, []).append(r)
	return rounds


def build_uniform_plan(employee, grant_date, settings, existing_calc=None):
	"""全社基準日の付与予定（前倒し）を作る。前回の付与日から今回の前日までを算定期間とし、短縮された期間は出勤とみなす。"""
	emp = employee_info(employee)
	grant_date = getdate(grant_date)
	rounds = confirmed_rounds(employee)
	last_round = max(rounds) if rounds else 0
	round_no = last_round + 1
	legal_base = nth_base_date(emp.hire_date, round_no)
	legal_period = legal_attendance_period(emp.hire_date, round_no)
	start = getdate(rounds[last_round][0].grant_date) if rounds else emp.hire_date
	period = (start, grant_date - timedelta(days=1))
	reasons = []
	attendance = None
	try:
		attendance = attendance_source.compute(employee, period[0], period[1], legal_period)
	except attendance_source.NoCalendar as e:
		reasons.append(str(e))
	contract = get_contract_at(employee, grant_date)
	terms = contract_terms(contract)
	actual = actual_annual_days(employee, terms, period, round_no == 1)
	decision, why = decide_category(terms, actual)
	if why:
		reasons.append(why)
	if not emp.service_confirmed:
		reasons.append("勤続通算の起算日が未確定です")
	months = service_months(emp.service_start, legal_base)
	tables = grant_tables(emp.company)
	table = select_grant_table(terms, tables) if terms else LEGAL_GRANT_TABLE
	days = table.days_for(months, decision.category) if decision else Decimal(0)
	status = ST_PLANNED
	if attendance is None:
		reasons.append("出勤率の算定がありません")
	elif attendance.is_pending:
		reasons.append(f"勤怠が未確定の日が {len(attendance.unrecorded_days)} 日あります")
	calc_doc = attendance_source.save_calc(employee, attendance, legal_period, name=existing_calc) if attendance else None
	if not reasons and attendance.eligible is False:
		status, days = ST_NO_GRANT, Decimal(0)
	elif reasons:
		status = ST_PENDING
	values = {
		"employee": employee, "company": emp.company, "grant_date": grant_date, "round_no": round_no, "is_manual": 0,
		"plan_method": "全社基準日",
		"hire_date": emp.hire_date, "service_start_date": emp.service_start, "service_months": months,
		"days": fl(days), "table_days": fl(days), "category": decision.category.value if decision else None,
		"category_basis": decision.basis if decision else None, "grant_table": table_doc_name(table),
		"attendance_calc": calc_doc.name if calc_doc else None, "attendance_rate": calc_doc.rate if calc_doc else None,
		"status": status, "pending_reasons": "\n".join(dict.fromkeys(reasons)) or None,
	}
	values.update(contract_values(contract))
	return values


# ------------------------------------------------------------------ 手動入力の初期値
def fill_manual_defaults(doc):
	"""手動入力（前倒し・分割・追加付与）の勤続・区分・付与テーブルの日数を初期値として入れる（LVR-21）。"""
	emp = employee_info(doc.employee)
	grant_date = getdate(doc.grant_date)
	doc.hire_date = emp.hire_date
	doc.service_start_date = emp.service_start
	contract = get_contract_at(doc.employee, grant_date)
	for key, value in contract_values(contract).items():
		doc.set(key, value)
	terms = contract_terms(contract)
	decision, _why = decide_category(terms, None)
	if decision:
		doc.category = decision.category.value
		doc.category_basis = decision.basis
	round_no = doc.round_no or guess_round(doc, emp)
	legal_base = nth_base_date(emp.hire_date, round_no)
	doc.service_months = service_months(emp.service_start, legal_base)
	tables = grant_tables(emp.company)
	table = select_grant_table(terms, tables) if terms else LEGAL_GRANT_TABLE
	doc.grant_table = table_doc_name(table)
	if decision:
		doc.table_days = fl(table.days_for(doc.service_months, decision.category))


def guess_round(doc, emp=None):
	"""付与の回の既定値。直前の回が法定日数に満たず、付与日がその回の法定の基準日以前なら同じ回（分割付与）。"""
	emp = emp or employee_info(doc.employee)
	grant_date = getdate(doc.grant_date)
	# 付与日以後で最初の法定の基準日の回（付与日が法定の基準日ならその回、前なら前倒し）
	by_date = 1
	while nth_base_date(emp.hire_date, by_date) < grant_date:
		by_date += 1
	rounds = confirmed_rounds(doc.employee, exclude=doc.name)
	last = max([r for r in rounds if r > 0], default=0)
	if not last:
		return by_date
	legal_base = nth_base_date(emp.hire_date, last)
	total = sum(D(r.days) for r in rounds[last])
	legal = D(rounds[last][0].legal_days) if rounds[last][0].legal_days else Decimal(10)
	if total < legal and grant_date <= legal_base:
		return last
	return max(last + 1, by_date)


# ------------------------------------------------------------------ 前倒し・分割付与の判定
def judge(doc):
	"""年休付与（下書き）の前倒し・分割付与の判定（LVR-22・23）。doc に結果を書き込み、エラーの一覧を返す。

	既存の確定済みの付与（調整を除く）とこの付与を回ごとに並べ、calc.judge_advance_grants で判定する。
	記録のない過去の回は、法定の基準日に法定日数を付与したものとして補う（導入前の付与など）。
	分割付与の途中（付与日が法定の基準日より前で累計が法定日数に満たない）は、残りを法定の基準日までに
	付与すればよいためエラーにせず警告とする。基準日を過ぎても満たない場合は日次処理で通知する。
	"""
	emp = employee_info(doc.employee)
	if not doc.round_no:
		doc.round_no = guess_round(doc, emp)
	rounds = confirmed_rounds(doc.employee, exclude=doc.name)
	category = WorkCategory(doc.category) if doc.category and doc.category != WorkCategory.NONE.value else WorkCategory.NORMAL
	inputs = []
	overrides = {}
	max_round = max([doc.round_no] + [r for r in rounds if r > 0])
	for no in range(1, max_round + 1):
		items = rounds.get(no, [])
		if not items and no != doc.round_no:
			legal_base = nth_base_date(emp.hire_date, no)
			inputs.append(GrantInput(legal_base, legal_days(service_months(emp.service_start, legal_base), category), no))
			continue
		for r in items:
			inputs.append(GrantInput(getdate(r.grant_date), D(r.days), no))
			if r.status == ST_NO_GRANT:
				overrides[no] = 0
	if doc.status == ST_NO_GRANT:
		overrides[doc.round_no] = 0
	this = GrantInput(getdate(doc.grant_date), D(doc.days), doc.round_no)
	inputs.append(this)
	inputs.sort(key=lambda g: (g.round_no, g.grant_date))
	try:
		result = judge_advance_grants(emp.hire_date, inputs, category, emp.service_start, LEGAL_GRANT_TABLE, overrides)
	except ValueError as e:
		return [str(e)]
	rj = result.rounds[doc.round_no - 1]
	doc.legal_base_date = rj.legal_base_date
	doc.legal_days = fl(rj.legal_days)
	doc.advance_period = str(rj.advance)
	ordered = sorted(rj.grants, key=lambda g: g.grant_date)
	idx = next(i for i, g in enumerate(ordered) if g is this)
	doc.cumulative_days = fl(rj.cumulative_after_each[idx])
	doc.ten_day_reach_date = rj.ten_day_reach_date
	doc.five_day_target = 1 if is_obligation_target(rj.cumulative_after_each[idx]) else 0
	if doc.grant_kind != GrantKind.ADJUSTMENT.value:
		doc.grant_kind = rj.kind.value
	errors, warnings = [], []
	for e in rj.errors:
		if SPLIT_SHORT_MARK in e and getdate(doc.grant_date) < rj.legal_base_date:
			warnings.append(_("分割付与の途中です。法定の基準日 {0} までに残り {1} 日を付与してください。").format(
				rj.legal_base_date, fl(rj.legal_days - rj.cumulative_by_legal_base)))
		else:
			errors.append(e)
	doc.judgement_ok = 0 if errors else 1
	lines = [_("法定の基準日 {0}、法定の日数 {1} 日、繰上げ期間 {2}、この回の累計 {3} 日").format(
		rj.legal_base_date, fl(rj.legal_days), rj.advance, doc.cumulative_days)]
	lines += [_("エラー：") + e for e in errors] + [_("注意：") + w for w in warnings]
	doc.judgement = "\n".join(lines)
	doc.manual_check_items = "\n".join(f"・{x}" for x in MANUAL_CHECK_ITEMS) if needs_manual_check(doc) else None
	return errors


def needs_manual_check(doc):
	"""システムで判定できない事項の確認が必要か（前倒し・分割付与）。"""
	return doc.grant_kind in (GrantKind.ADVANCED.value, GrantKind.SPLIT.value)
