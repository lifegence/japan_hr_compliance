"""年休の取得（hrms 標準の Leave Application）と付与単位の充当（LVR-31・33・40・41・52）。

- 申請の保存時：付与単位の残高検査（日ごとに、その日に使える付与分のうち古いものから充当できるか）。
  hrms 標準の残高チェックは申請の開始日時点の残高で判定するため、基準日をまたぐ申請（観点 #15）で
  新しい付与分を数えられず、また失効日以後の日に失効予定の付与分を数えてしまう。年休の休暇種別は
  allow_negative=1（hrms の判定は警告のみ）とし、ここでの検査を正とする（3.1 の検証結果）。
- 確定（Submit・承認）時：古い付与分から充当し、年休充当（JP Annual Leave Consumption）を記録する。
- 取消時：充当を戻す。失効済みの付与分には戻さず、その分を器に負の行で記録して担当者に通知する（LVR-33）。
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from japan_hr_compliance.jp_leave import container
from japan_hr_compliance.jp_leave.calc.ledger import GrantState, InsufficientBalance, LeaveLedger, TakeDay
from japan_hr_compliance.jp_leave.utils import (
	CONSUMPTION,
	GRANT,
	ST_ACTIVE,
	ST_EXPIRED,
	ST_EXTINGUISHED,
	D,
	fl,
	get_settings,
	hr_recipients,
	is_annual_leave_type,
	notify,
	today_,
)

STATE = {ST_ACTIVE: GrantState.ACTIVE, ST_EXPIRED: GrantState.EXPIRED, ST_EXTINGUISHED: GrantState.EXTINGUISHED}
DEFAULT_KIND = "本人の請求"


def load_ledger(employee, leave_type=None):
	"""確定済みの年休付与から付与単位の台帳（calc.LeaveLedger）を組み立てる。"""
	ledger = LeaveLedger()
	filters = {"employee": employee, "docstatus": 1, "status": ("in", list(STATE)), "days": (">", 0)}
	if leave_type:
		filters["leave_type"] = leave_type
	for g in frappe.get_all(GRANT, filters=filters, fields=["name", "grant_date", "days", "expiry_date", "remaining_days",
			"status"], order_by="grant_date asc, creation asc"):
		grant = ledger.add_grant(g.name, getdate(g.grant_date), D(g.days))
		grant.expiry = getdate(g.expiry_date)
		grant.remaining = D(g.remaining_days)
		grant.state = STATE[g.status]
	return ledger


def take_days(doc):
	"""申請の日ごとの取得日数。休日を含めない休暇種別は会社カレンダーの休日を除く。半日は 0.5 日（LVR-40）。"""
	from japan_hr_compliance.jp_leave.attendance_source import holiday_dates

	start, end = getdate(doc.from_date), getdate(doc.to_date)
	include_holiday = cint(frappe.get_cached_value("Leave Type", doc.leave_type, "include_holiday"))
	holidays = set() if include_holiday else holiday_dates(doc.employee, start, end)
	half_date = None
	if cint(doc.half_day):
		half_date = getdate(doc.half_day_date) if doc.half_day_date else start
	takes = []
	d = start
	while d <= end:
		if d not in holidays:
			if d == half_date:
				takes.append(TakeDay(d, D("0.5"), doc.get("jp_half_day_part") or None))
			else:
				takes.append(TakeDay(d, D(1)))
		d += timedelta(days=1)
	return takes


def validate(doc, method=None):
	"""Leave Application の保存時（年休のみ）：取得区分・半日・付与単位の残高を検査する。"""
	if not is_annual_leave_type(doc.leave_type):
		return
	if not doc.get("jp_take_kind"):
		doc.jp_take_kind = DEFAULT_KIND
	settings = get_settings(doc.company)
	if cint(doc.half_day):
		if not cint(settings.half_day_allowed):
			frappe.throw(_("この会社は半日単位の年次有給休暇を認めていません（JP Leave Settings）。"))
		if not doc.get("jp_half_day_part"):
			frappe.throw(_("半日の年次有給休暇は午前・午後のどちらかを選んでください（LVR-40）。"))
	if doc.status in ("Rejected", "Cancelled") or doc.docstatus != 0:
		return
	takes = take_days(doc)
	if not takes:
		return
	ledger = load_ledger(doc.employee, doc.leave_type)
	try:
		ledger.consume(doc.name or "new", takes)
	except InsufficientBalance as e:
		frappe.throw(
			_("年次有給休暇の残日数が足りません。{0}。付与日より前の日には新しい付与分を使えず、最終使用日を過ぎた付与分は使えません。")
			.format(e),
			title=_("残日数の不足（付与単位）"),
		)


def on_submit(doc, method=None):
	"""承認された年休の申請を古い付与分から充当する（LVR-31）。"""
	if not is_annual_leave_type(doc.leave_type) or doc.status != "Approved":
		return
	ledger = load_ledger(doc.employee, doc.leave_type)
	try:
		rows = ledger.consume(doc.name, take_days(doc))
	except InsufficientBalance as e:
		frappe.throw(_("年次有給休暇の残日数が足りません。{0}").format(e))
	total = sum((r.amount for r in rows), D(0))
	if abs(fl(total) - flt(doc.total_leave_days)) > 0.001:
		frappe.throw(_("充当日数（{0}）と申請の日数（{1}）が一致しません。会社カレンダーを確認してください。").format(fl(total), doc.total_leave_days))
	for r in rows:
		frappe.get_doc({
			"doctype": CONSUMPTION, "employee": doc.employee, "leave_application": doc.name, "grant": r.grant_id,
			"leave_date": r.day, "days": fl(r.amount), "half_day_part": r.half_day_part,
			"take_kind": doc.get("jp_take_kind") or DEFAULT_KIND,
		}).insert(ignore_permissions=True)
	_write_back(ledger, {r.grant_id for r in rows})
	_after_change(doc.employee)


def on_cancel(doc, method=None):
	"""取消時に充当を戻す。失効済みの付与分には戻さない（LVR-33）。"""
	rows = frappe.get_all(CONSUMPTION, filters={"leave_application": doc.name, "cancelled": 0},
		fields=["name", "grant", "leave_date", "days", "half_day_part"])
	if not rows:
		return
	ledger = load_ledger(doc.employee)
	from japan_hr_compliance.jp_leave.calc.ledger import Consumption

	for r in rows:
		ledger.consumptions.append(Consumption(doc.name, r.grant, getdate(r.leave_date), D(r.days), r.half_day_part))
	as_of = today_()
	result = ledger.cancel(doc.name, as_of)
	lost_ids = {id(c) for c in result.not_restored}
	names = iter(r.name for r in rows)
	for c in ledger.consumptions:  # rows と同じ順に追加してある
		if c.application_id != doc.name:
			continue
		frappe.db.set_value(CONSUMPTION, next(names), {"cancelled": 1, "cancelled_on": as_of,
			"not_restored": 1 if id(c) in lost_ids else 0}, update_modified=False)
	_write_back(ledger, {c.grant_id for c in result.restored})
	lost = {}
	for c in result.not_restored:
		lost[c.grant_id] = lost.get(c.grant_id, D(0)) + c.amount
	for grant_name, amount in lost.items():
		grant = frappe.get_doc(GRANT, grant_name)
		entry = container.add_negative(grant, fl(amount), as_of)
		grant.db_set({"used_days": fl(D(grant.used_days) - amount), "lapsed_days": fl(D(grant.lapsed_days) + amount)},
			update_modified=False)
		grant.add_comment("Info", _("休暇申請 {0} の取消で {1} 日を戻しませんでした（失効済みの付与分。台帳行 {2}）。")
			.format(doc.name, fl(amount), entry))
	if lost:
		notify(hr_recipients(), _("年休の取消で失効済みの付与分に戻らない日数があります：{0}（{1}）").format(
			doc.employee_name, doc.name), "Leave Application", doc.name)
	_after_change(doc.employee)


def _write_back(ledger, grant_ids):
	for gid in grant_ids:
		g = ledger.grants[gid]
		used = flt(frappe.db.sql(
			"select coalesce(sum(days), 0) from `tabJP Annual Leave Consumption` where `grant`=%s and cancelled=0", gid
		)[0][0])
		frappe.db.set_value(GRANT, gid, {"remaining_days": fl(g.remaining), "used_days": used}, update_modified=False)


def _after_change(employee):
	from japan_hr_compliance.jp_leave import five_day_service

	five_day_service.update_taken(employee)
