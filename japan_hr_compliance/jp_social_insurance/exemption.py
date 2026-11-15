"""産前産後休業・育児休業等の休業期間を休暇の記録と勤怠から集める（SIR-50〜52）。

- 休業の種類は Leave Type の「社保免除の区分」（jp_si_exemption_kind）で判定する
- 休暇申請（承認済み・確定済み）の期間を休業とする。hrms は申請期間内の勤怠を「On Leave」に揃えるため、
  早期の復帰は申請の取消・修正（終了日の変更）で反映する。申請期間に紐づく勤怠（On Leave）が
  あれば「確定」、なければ「見込み」として示す
- 申請期間内に出勤の勤怠（Present / Work From Home）が残っている日は休業を区切る
- 出生時育児休業中の就業日は、休暇申請の「出生時育児休業中の就業日」（jp_si_work_days）と出勤の勤怠から
  集め、休業を区切らず14日の計算から除く日として渡す
- 日が連続する複数の申請の通算と免除月の判定は計算エンジン（calc.exemption）が行う
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe
from frappe.utils import getdate

from japan_hr_compliance.jp_social_insurance import calc
from japan_hr_compliance.jp_social_insurance import constants as C

WORK_STATUSES = ("Present", "Work From Home")


def parse_dates(text) -> list:
	"""改行・カンマ区切りの日付（YYYY-MM-DD）を読む。読めない行は無視せずエラーにする。"""
	out = []
	for token in (text or "").replace(",", "\n").split("\n"):
		token = token.strip()
		if token:
			try:
				out.append(getdate(token))
			except Exception:
				frappe.throw(frappe._("出生時育児休業中の就業日の日付が読めません：{0}").format(token))
	return out


@dataclass(frozen=True)
class LeaveSource:
	period: calc.LeavePeriod
	leave_application: str
	confirmed: bool


def exemption_leave_types() -> dict[str, str]:
	"""Leave Type → calc の休業の種類。"""
	rows = frappe.get_all("Leave Type", filters={"jp_si_exemption_kind": ("is", "set")},
		fields=["name", "jp_si_exemption_kind"])
	return {r.name: C.LEAVE_KIND_TO_CALC[r.jp_si_exemption_kind] for r in rows if r.jp_si_exemption_kind in C.LEAVE_KIND_TO_CALC}


def _split(start, end, worked):
	"""worked（出勤日）で [start, end] を区切った期間の列。"""
	from datetime import timedelta

	out, cur = [], start
	for d in sorted(w for w in worked if start <= w <= end):
		if cur < d:
			out.append((cur, d - timedelta(days=1)))
		cur = d + timedelta(days=1)
	if cur <= end:
		out.append((cur, end))
	return out


def get_leave_sources(employee: str) -> list[LeaveSource]:
	kinds = exemption_leave_types()
	if not kinds:
		return []
	apps = frappe.get_all("Leave Application",
		filters={"employee": employee, "leave_type": ("in", list(kinds)), "docstatus": 1, "status": "Approved"},
		fields=["name", "leave_type", "from_date", "to_date", "jp_si_work_days"], order_by="from_date asc")
	out = []
	for app in apps:
		kind = kinds[app.leave_type]
		start, end = getdate(app.from_date), getdate(app.to_date)
		attendance = frappe.get_all("Attendance", filters={"employee": employee, "docstatus": 1,
			"attendance_date": ("between", [start, end])}, fields=["attendance_date", "status"])
		confirmed = any(a.status == "On Leave" for a in attendance)
		worked = tuple(getdate(a.attendance_date) for a in attendance if a.status in WORK_STATUSES)
		if kind == calc.POSTNATAL_CHILDCARE:
			declared = tuple(d for d in parse_dates(app.jp_si_work_days) if start <= d <= end)
			work_days = tuple(sorted(set(worked + declared)))
			out.append(LeaveSource(calc.LeavePeriod(start, end, kind, work_days), app.name, confirmed))
			continue
		for s, e in _split(start, end, worked):
			out.append(LeaveSource(calc.LeavePeriod(s, e, kind), app.name, confirmed))
	return out


def get_leave_periods(employee: str) -> list[calc.LeavePeriod]:
	return [s.period for s in get_leave_sources(employee)]


def exemption_months(employee: str) -> dict:
	"""月額の免除月 → 理由（calc.monthly_exemption_months）。"""
	return calc.monthly_exemption_months(get_leave_periods(employee))


@frappe.whitelist()
def get_exemption_summary(employee: str):
	"""免除月と、見込み（勤怠で未確定）の休業があるかを返す（画面・確認用）。"""
	frappe.has_permission("Employee", "read", employee, throw=True)
	sources = get_leave_sources(employee)
	months = calc.monthly_exemption_months([s.period for s in sources])
	return {
		"months": [{"month": str(m), "reason": "月末在休" if r == "month_end" else "同月14日以上"} for m, r in months.items()],
		"estimated": [s.leave_application for s in sources if not s.confirmed],
	}
