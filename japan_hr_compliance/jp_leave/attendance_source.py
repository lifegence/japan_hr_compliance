"""出勤率の算定の入力（会社カレンダーと勤怠）を hrms から集め、算定明細を保存する（LVR-10〜16）。

- 所定労働日：従業員の Holiday List（会社カレンダー。hrms の Holiday List Assignment を含む）にない日
- 日次の勤怠：hrms の Attendance（確定済み）
    Present / Work From Home / Half Day → 出勤（半日年休の日・遅刻早退の日は出勤日）
    On Leave → 休暇種別の設定（年休／法定休暇の区分／出勤率での扱い）
    Absent → 欠勤
    Attendance の「出勤率の算定上の区分」（jp_day_class）が入っていればそれを優先する
  所定労働日に確定済みの Attendance がない日は「未確定」（付与予定を保留にする。LVR-16）。
- シフト（Shift Assignment）による所定労働日は第1次では見ない（B-2 の範囲。会社カレンダーのみ）。
"""

import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import getdate

from japan_hr_compliance.jp_leave.calc.attendance import (
	AttendanceRateResult,
	DayRecord,
	DayStatus,
	LeaveTreatment,
	calc_attendance_rate,
)
from japan_hr_compliance.jp_leave.setup import (
	SK_CHILDCARE,
	SK_FAMILY_CARE,
	SK_INJURY,
	SK_MATERNITY,
	SK_PATERNITY,
)
from japan_hr_compliance.jp_leave.utils import CALC, CATEGORY_ANNUAL

STATUTORY_STATUS = {
	SK_MATERNITY: DayStatus.MATERNITY,
	SK_CHILDCARE: DayStatus.CHILDCARE,
	SK_PATERNITY: DayStatus.CHILDCARE,
	SK_FAMILY_CARE: DayStatus.FAMILY_CARE,
	SK_INJURY: DayStatus.OCCUPATIONAL_INJURY,
}
DAY_CLASS_STATUS = {
	"不可抗力による休業": DayStatus.FORCE_MAJEURE,
	"使用者側に起因する経営・管理上の障害による休業": DayStatus.EMPLOYER_CAUSED,
	"正当な争議行為": DayStatus.STRIKE,
	"労働者の責に帰すべき事由によらない不就労": DayStatus.NOT_ATTRIBUTABLE,
}
WORKED = ("Present", "Work From Home", "Half Day")


class NoCalendar(frappe.ValidationError):
	pass


def iter_days(start, end):
	d = getdate(start)
	end = getdate(end)
	while d <= end:
		yield d
		d += timedelta(days=1)


def holiday_dates(employee, start, end):
	"""期間内の所定休日（Holiday List の日付）。期間の途中で Holiday List が変わる場合も hrms の割当の期間ごとに判定する。"""
	from hrms.utils.holiday_list import get_holiday_dates_between_range

	try:
		dates = get_holiday_dates_between_range(employee, getdate(start), getdate(end))
	except frappe.ValidationError as e:
		frappe.clear_last_message()
		raise NoCalendar(_("会社カレンダー（Holiday List）がありません：{0}").format(e)) from e
	return {getdate(d) for d in dates}


def scheduled_days(employee, start, end):
	"""所定労働日（Holiday List にない日）。"""
	if getdate(end) < getdate(start):
		return []
	holidays = holiday_dates(employee, start, end)
	return [d for d in iter_days(start, end) if d not in holidays]


def leave_type_info():
	rows = frappe.get_all("Leave Type", fields=["name", "jp_leave_category", "jp_statutory_kind", "jp_attendance_treatment"])
	return {r.name: r for r in rows}


def day_records(employee, start, end):
	"""確定済みの Attendance を日ごとの勤怠区分に変換する。"""
	info = leave_type_info()
	records = {}
	rows = frappe.get_all(
		"Attendance",
		filters={"employee": employee, "docstatus": 1, "attendance_date": ("between", (start, end))},
		fields=["attendance_date", "status", "leave_type", "jp_day_class"],
	)
	for r in rows:
		d = getdate(r.attendance_date)
		if r.jp_day_class:
			records[d] = DayRecord(DAY_CLASS_STATUS[r.jp_day_class])
		elif r.status in WORKED:
			records[d] = DayRecord(DayStatus.WORKED)
		elif r.status == "On Leave":
			lt = info.get(r.leave_type) or frappe._dict()
			if lt.jp_leave_category == CATEGORY_ANNUAL:
				records[d] = DayRecord(DayStatus.ANNUAL_LEAVE)
			elif lt.jp_statutory_kind in STATUTORY_STATUS:
				records[d] = DayRecord(STATUTORY_STATUS[lt.jp_statutory_kind])
			else:
				records[d] = DayRecord(DayStatus.OTHER_LEAVE, r.leave_type or "（種別未指定）")
		elif r.status == "Absent":
			records[d] = DayRecord(DayStatus.ABSENT)
	return records


def treatments():
	"""法令に定めのない休暇の出勤率での扱い（LVR-13）。"""
	result = {}
	for name, lt in leave_type_info().items():
		if lt.jp_leave_category == CATEGORY_ANNUAL or lt.jp_statutory_kind in STATUTORY_STATUS:
			continue
		result[name] = LeaveTreatment(lt.jp_attendance_treatment or LeaveTreatment.DEEMED.value)
	return result


def compute(employee, period_start, period_end, legal_period=None):
	"""出勤率を算定する（保存しない）。legal_period は前倒しの場合の法定の算定期間（短縮期間のみなし出勤）。"""
	cal_start, cal_end = getdate(period_start), getdate(period_end)
	if legal_period:
		cal_start = min(cal_start, getdate(legal_period[0]))
		cal_end = max(cal_end, getdate(legal_period[1]))
	days = scheduled_days(employee, cal_start, cal_end) if cal_end >= cal_start else []
	records = day_records(employee, period_start, period_end) if getdate(period_end) >= getdate(period_start) else {}
	legal = (getdate(legal_period[0]), getdate(legal_period[1])) if legal_period else None
	return calc_attendance_rate(getdate(period_start), getdate(period_end), days, records, treatments(), legal)


def result_label(result: AttendanceRateResult):
	if result.is_pending:
		return "保留"
	if result.eligible is None:
		return "判定不能"
	return "付与" if result.eligible else "付与なし"


def save_calc(employee, result: AttendanceRateResult, legal_period=None, name=None):
	"""算定明細を JP Attendance Rate Calc に保存する（LVR-15）。name を渡すと未確定の明細を更新する。"""
	doc = frappe.get_doc(CALC, name) if name else frappe.new_doc(CALC)
	if doc.frozen:
		frappe.throw(_("確定済みの付与に使った算定明細 {0} は変更できません。").format(doc.name))
	rate = result.rate
	doc.update({
		"employee": employee, "period_start": result.period_start, "period_end": result.period_end,
		"legal_period_start": legal_period[0] if legal_period else None,
		"legal_period_end": legal_period[1] if legal_period else None,
		"result": result_label(result), "scheduled_days": result.scheduled_days,
		"excluded_days": sum(result.excluded.values()), "total_working_days": result.total_working_days,
		"holiday_work_days": result.holiday_work_days, "worked_days": result.worked_days,
		"deemed_days": sum(result.deemed.values()), "attended_days": result.attended_days,
		"absent_days": result.absent_days, "rate": float(rate * 100) if rate is not None else None,
		"shortened_days": result.shortened_days, "shortened_deemed_days": result.shortened_deemed_days,
		"unrecorded_days": len(result.unrecorded_days),
		"unrecorded_dates": ", ".join(str(d) for d in result.unrecorded_days[:60]) or None,
		"treatments": json.dumps(result.treatments, ensure_ascii=False) if result.treatments else None,
		"notes": "\n".join(result.notes) or None,
	})
	doc.set("details", [])
	for label, n in result.excluded.items():
		doc.append("details", {"kind": "除外", "label": label, "days": n})
	for label, n in result.deemed.items():
		doc.append("details", {"kind": "みなし出勤", "label": label, "days": n})
	for label, n in result.absent_breakdown.items():
		doc.append("details", {"kind": "欠勤", "label": label, "days": n})
	if result.holiday_work_days:
		doc.append("details", {"kind": "所定休日労働", "label": "所定休日に労働した日（算入しない）", "days": result.holiday_work_days})
	doc.flags.ignore_permissions = True
	doc.save()
	return doc


def worked_days_count(employee, start, end):
	"""実績の労働日数（LVR-08 の年間所定労働日数の算出）。確定済みの Attendance の出勤日。"""
	if getdate(end) < getdate(start):
		return 0
	return frappe.db.count(
		"Attendance",
		{"employee": employee, "docstatus": 1, "status": ("in", WORKED), "attendance_date": ("between", (start, end))},
	)
