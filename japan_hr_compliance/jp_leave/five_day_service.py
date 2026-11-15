"""年5日の取得義務の履行期間・取得日数・通知（LVR-50〜53）。

- 履行期間の起点：付与の回ごとに、付与日数の累計が10日に達した日（個別基準日は基準日、前倒しは付与日、
  分割は10日到達日）。取得は回の最初の付与日から数える（10日到達日より前の取得を含める。LVR-52）。
- 期間が重なる場合は会社設定により按分（既定）または各期間で5日（LVR-51）。計算は calc.five_day。
- 取得日数は年休充当（取消済みを除く）から数える。半日は0.5日、特別休暇・時間単位は数えない。
- 通知は会社設定の3時点（既定：開始から6か月・9か月、終了の2か月前）。未取得の日数があれば本人・上長・
  人事担当に通知し、履行期間に通知履歴を残す。
"""

from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import getdate

from japan_hr_compliance.jp_leave.calc.dates import add_months
from japan_hr_compliance.jp_leave.calc.five_day import (
	NOTICE_MESSAGES,
	LeaveTake,
	NoticeStage,
	ObligationPeriod,
	ObligationSource,
	OverlapMethod,
	TakeKind,
	TakeUnit,
	build_obligation_periods,
	obligation_status,
)
from japan_hr_compliance.jp_leave.utils import (
	CONSUMPTION,
	GRANT,
	OBLIGATION,
	ST_ACTIVE,
	ST_EXPIRED,
	ST_EXTINGUISHED,
	D,
	employee_user,
	fl,
	get_settings,
	hr_recipients,
	manager_user,
	notify,
	today_,
)

TEN = D(10)


def sources_for(employee):
	"""付与の回ごとの履行期間の起点（calc.ObligationSource）と、その回の付与の一覧。"""
	grants = frappe.get_all(
		GRANT,
		filters={"employee": employee, "docstatus": 1, "status": ("in", (ST_ACTIVE, ST_EXPIRED, ST_EXTINGUISHED)),
			"days": (">", 0), "grant_kind": ("!=", "調整")},
		fields=["name", "grant_date", "days", "round_no"],
		order_by="grant_date asc, creation asc",
	)
	rounds = OrderedDict()
	for g in grants:
		rounds.setdefault(g.round_no or g.name, []).append(g)
	result = []
	for items in rounds.values():
		total = D(0)
		for g in items:
			total += D(g.days)
			if total >= TEN:
				first = getdate(items[0].grant_date)
				src = ObligationSource(getdate(g.grant_date), first, g.name)
				result.append((src, [(x, total if x is g else None) for x in items if getdate(x.grant_date) <= getdate(g.grant_date)]))
				break
	return result


def rebuild(employee):
	"""年休付与の確定・取消のたびに履行期間を作り直す（通知履歴のある期間は残す）。"""
	company = frappe.db.get_value("Employee", employee, "company")
	settings = get_settings(company)
	sources = sources_for(employee)
	by_start = {s.start: (s, grants) for s, grants in sources}
	periods = build_obligation_periods(
		[s for s, _g in sources], OverlapMethod(settings.overlap_method or OverlapMethod.PRORATE.value),
		bool(settings.half_day_allowed),
	)
	existing = {getdate(d.period_start): d.name for d in frappe.get_all(OBLIGATION, filters={"employee": employee},
		fields=["name", "period_start"])}
	keep = set()
	for p in periods:
		name = existing.get(p.start)
		doc = frappe.get_doc(OBLIGATION, name) if name else frappe.new_doc(OBLIGATION)
		doc.update({
			"employee": employee, "company": company, "period_start": p.start, "period_end": p.end,
			"count_from": p.count_from, "months": p.months, "required_days": fl(p.required_days),
			"method": settings.overlap_method, "is_prorated": 1 if p.is_prorated else 0,
		})
		if doc.status == "無効":
			doc.status = "履行中"
		doc.set("grants", [])
		for src in p.sources:
			_s, grants = by_start[src.start]
			for g, cumulative in grants:
				doc.append("grants", {"grant": g.name, "base_date": src.start, "count_from": src.count_from,
					"days": fl(cumulative) if cumulative is not None else fl(g.days)})
		doc.flags.ignore_permissions = True
		doc.save()
		keep.add(doc.name)
	for start, name in existing.items():
		if name in keep:
			continue
		if frappe.get_all("JP Five Day Notice Log", filters={"parent": name}, limit=1):
			frappe.db.set_value(OBLIGATION, name, "status", "無効", update_modified=False)
		else:
			frappe.delete_doc(OBLIGATION, name, ignore_permissions=True, flags={"jp_obsolete": True})
	update_taken(employee)


def takes_of(employee):
	"""年5日に数える取得（年休充当の取消済みを除く）。"""
	rows = frappe.get_all(CONSUMPTION, filters={"employee": employee, "cancelled": 0},
		fields=["leave_date", "days", "take_kind"])
	takes = []
	for r in rows:
		kind = TakeKind(r.take_kind) if r.take_kind else TakeKind.REQUEST
		unit = TakeUnit.HALF if fl(r.days) == 0.5 else TakeUnit.FULL
		takes.append(LeaveTake(getdate(r.leave_date), unit, kind))
	return takes


def period_of(doc):
	return ObligationPeriod(getdate(doc.period_start), getdate(doc.period_end), D(doc.required_days),
		getdate(doc.count_from or doc.period_start), (), doc.months or 12)


def update_taken(employee, as_of=None):
	as_of = getdate(as_of or today_())
	takes = takes_of(employee)
	for doc in frappe.get_all(OBLIGATION, filters={"employee": employee, "status": ("!=", "無効")},
			fields=["name", "period_start", "period_end", "count_from", "required_days", "months"]):
		period = period_of(doc)
		status = obligation_status(period, takes, as_of)
		total = sum((t.counted_days for t in takes if period.count_from <= t.day <= period.end), D(0))
		if status.taken_days >= period.required_days:
			state = "達成"
		elif as_of > period.end:
			state = "未達"
		else:
			state = "履行中"
		frappe.db.set_value(OBLIGATION, doc.name, {
			"taken_days": fl(status.taken_days), "planned_days": fl(total - status.taken_days),
			"shortfall": fl(status.shortfall), "status": state,
		}, update_modified=False)


def notice_points(doc, settings):
	"""会社設定の通知時点（LVR-53）。「期間終了の2か月前」は期間の末日の2か月前の応答日。"""
	start, end = getdate(doc.period_start), getdate(doc.period_end)
	return [
		(NoticeStage.EARLY, add_months(start, int(settings.notice_1_months or 6)),
			settings.notice_1_text or NOTICE_MESSAGES[NoticeStage.EARLY]),
		(NoticeStage.ACTION, add_months(start, int(settings.notice_2_months or 9)),
			settings.notice_2_text or NOTICE_MESSAGES[NoticeStage.ACTION]),
		(NoticeStage.URGENT, add_months(end, -int(settings.notice_3_months_before_end or 2)),
			settings.notice_3_text or NOTICE_MESSAGES[NoticeStage.URGENT]),
	]


def send_notices(as_of=None):
	"""未取得の日数が残る履行期間について、到達した段階の通知を送る（各段階1回）。

	通知されないまま複数の段階を過ぎていた場合（導入直後など）は最も進んだ段階だけを送り、
	前の段階は「省略」として履歴に残す。
	"""
	as_of = getdate(as_of or today_())
	sent = []
	for row in frappe.get_all(OBLIGATION, filters={"status": "履行中", "period_start": ("<=", as_of)}, pluck="name"):
		doc = frappe.get_doc(OBLIGATION, row)
		update_taken(doc.employee, as_of)
		doc.reload()
		if doc.status != "履行中" or doc.shortfall <= 0:
			continue
		settings = get_settings(doc.company)
		logged = {n.stage for n in doc.notices}
		due = [(stage, day, text) for stage, day, text in notice_points(doc, settings) if day <= as_of and stage.value not in logged]
		if not due:
			continue
		for stage, day, text in due[:-1]:
			doc.append("notices", {"stage": stage.value, "notice_date": as_of, "shortfall": doc.shortfall, "recipients": "（省略）"})
		stage, day, text = due[-1]
		users = [employee_user(doc.employee), manager_user(doc.employee)] + hr_recipients()
		subject = _("【年5日の取得義務・{0}】{1}：履行期間 {2}〜{3}、必要 {4} 日、取得 {5} 日、未取得 {6} 日").format(
			stage.value, doc.employee_name, doc.period_start, doc.period_end, doc.required_days, doc.taken_days, doc.shortfall)
		recipients = notify(users, subject, OBLIGATION, doc.name, f"{subject}\n{text}")
		doc.append("notices", {"stage": stage.value, "notice_date": as_of, "shortfall": doc.shortfall,
			"recipients": ", ".join(recipients) or "（通知先なし）"})
		doc.flags.ignore_permissions = True
		doc.save()
		sent.append((doc.name, stage.value))
	return sent


def update_all(as_of=None):
	for employee in frappe.get_all(OBLIGATION, filters={"status": "履行中"}, pluck="employee", distinct=True):
		update_taken(employee, as_of)


def shortfall_list(as_of=None, company=None):
	"""未達見込みの一覧（LVR-53）：経過率・残り期間・未取得日数。"""
	as_of = getdate(as_of or today_())
	filters = {"status": ("in", ("履行中", "未達")), "period_start": ("<=", as_of)}
	if company:
		filters["company"] = company
	rows = []
	for d in frappe.get_all(OBLIGATION, filters=filters, fields=["name", "employee", "employee_name", "company", "period_start",
			"period_end", "count_from", "required_days", "taken_days", "planned_days", "shortfall", "months", "status"],
			order_by="period_end asc"):
		period = period_of(d)
		st = obligation_status(period, takes_of(d.employee), as_of)
		if st.is_met:
			continue
		reached = [stage.value for stage, day, _t in notice_points(d, get_settings(company or d.company)) if day <= as_of]
		d.update({"elapsed_rate": float(st.elapsed_ratio * 100), "days_left": st.days_left,
			"stage": reached[-1] if reached else None, "taken_days": fl(st.taken_days), "shortfall": fl(st.shortfall)})
		rows.append(d)
	return rows
