"""休暇種別・法定休暇・従業員の設定の検査（LVR-06・13・43・70〜74）。"""

from datetime import date

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from japan_hr_compliance.jp_leave.calc.attendance import LeaveTreatment, check_treatment_change
from japan_hr_compliance.jp_leave.setup import (
	LEGALLY_DEEMED_KINDS,
	PERIOD_KINDS,
	SK_CARE_DAY,
	SK_NURSING,
	TREAT_DEEMED,
)
from japan_hr_compliance.jp_leave.utils import CATEGORY_ANNUAL, CATEGORY_SPECIAL, CATEGORY_STATUTORY, GRANT, get_settings

LIMIT_PER_PERSON = 5
LIMIT_MAX = 10


# ------------------------------------------------------------------ Leave Type
def validate_leave_type(doc, method=None):
	if doc.jp_leave_category == CATEGORY_ANNUAL and cint(doc.is_lwp):
		frappe.throw(_("年次有給休暇は無給（Leave Without Pay）にできません。取得日は通常の賃金を支払います（労基法39条9項、LVR-43）。"))
	if doc.jp_statutory_kind in LEGALLY_DEEMED_KINDS and (doc.jp_attendance_treatment or TREAT_DEEMED) != TREAT_DEEMED:
		frappe.throw(_("{0} は労基法39条10項により出勤したものとみなす休業です。出勤率での扱いは変更できません。").format(doc.jp_statutory_kind))
	if doc.jp_leave_category in (CATEGORY_STATUTORY, CATEGORY_SPECIAL) and doc.jp_attendance_treatment:
		changed = doc.is_new() or doc.has_value_changed("jp_attendance_treatment")
		warning = check_treatment_change(doc.name or doc.leave_type_name, LeaveTreatment(doc.jp_attendance_treatment))
		if warning and changed:
			frappe.msgprint(warning.message, title=_("不利益取扱いのおそれ（LVR-13）"), indicator="orange")
			doc.flags.jp_treatment_warning = warning.message


def on_update_leave_type(doc, method=None):
	"""出勤率での扱いの変更を、設定者・日時とともに記録する（LVR-13）。"""
	if doc.flags.jp_treatment_warning:
		doc.add_comment("Comment", _("出勤率での扱いを「{0}」に設定（設定者：{1}）。{2}").format(
			doc.jp_attendance_treatment, frappe.session.user, doc.flags.jp_treatment_warning))


# ------------------------------------------------------------------ Leave Application（法定休暇）
def validate_leave_application(doc, method=None):
	kind = frappe.get_cached_value("Leave Type", doc.leave_type, "jp_statutory_kind")
	if kind in PERIOD_KINDS:
		if not doc.get("jp_planned_from_date"):
			doc.jp_planned_from_date = doc.from_date
		if not doc.get("jp_planned_to_date"):
			doc.jp_planned_to_date = doc.to_date
	if kind in (SK_NURSING, SK_CARE_DAY) and doc.status not in ("Rejected", "Cancelled"):
		check_family_leave_limit(doc, kind)


def school_year_end_of_third_grade(birth_date):
	"""小学校3年生修了の日（3月31日）。4月1日生まれまでは早生まれとして前の学年に入る。"""
	b = getdate(birth_date)
	entry = b.year + 6 if (b.month, b.day) <= (4, 1) else b.year + 7
	return date(entry + 3, 3, 31)


def eligible_children(employee, on_date):
	"""子の看護等休暇の対象となる子（B-1 の家族情報の続柄「子」で、小学校3年生修了まで）。"""
	on_date = getdate(on_date)
	rows = frappe.get_all("JP Employee Dependent", filters={"parent": employee, "parenttype": "Employee", "relation": "子"},
		fields=["dependent_name", "birth_date"])
	return [r for r in rows if r.birth_date and getdate(r.birth_date) <= on_date <= school_year_end_of_third_grade(r.birth_date)]


def family_count(employee, kind, on_date):
	if kind == SK_NURSING:
		return len(eligible_children(employee, on_date))
	return cint(frappe.db.get_value("Employee", employee, "jp_care_family_count"))


def leave_year(company, on_date):
	start_month = int(get_settings(company).care_leave_year_start_month or 4)
	d = getdate(on_date)
	year = d.year if d.month >= start_month else d.year - 1
	start = date(year, start_month, 1)
	end = date(year + 1, start_month, 1)
	return start, getdate(frappe.utils.add_days(end, -1))


def annual_limit(employee, kind, on_date):
	"""年度の上限日数：対象が1人なら5日、2人以上なら10日（LVR-72）。"""
	n = family_count(employee, kind, on_date)
	return n, min(n * LIMIT_PER_PERSON, LIMIT_MAX)


def check_family_leave_limit(doc, kind):
	n, limit = annual_limit(doc.employee, kind, doc.from_date)
	if n == 0:
		frappe.msgprint(_("{0} の対象となる子・家族が登録されていません（従業員の家族情報）。上限日数を判定できません。").format(kind),
			indicator="orange")
		return
	start, end = leave_year(doc.company, doc.from_date)
	used = flt(frappe.db.sql(
		"""select coalesce(sum(total_leave_days), 0) from `tabLeave Application`
		where employee=%s and leave_type=%s and docstatus < 2 and status not in ('Rejected', 'Cancelled')
		and from_date between %s and %s and name != %s""",
		(doc.employee, doc.leave_type, start, end, doc.name or ""),
	)[0][0])
	if used + flt(doc.total_leave_days) > limit:
		frappe.throw(_("{0} の年度（{1}〜{2}）の上限は {3} 日です（対象 {4} 人）。取得済み・申請中 {5} 日に {6} 日を加えると上限を超えます（LVR-72）。")
			.format(kind, start, end, limit, n, used, doc.total_leave_days), title=_("上限日数の超過"))


def get_statutory_leave_periods(employee, from_date, to_date, si_only=False):
	"""法定休業の期間（予定と実績）。SI-008（社会保険料の免除）などの判定に渡す（LVR-71・73）。"""
	kinds = frappe.get_all("Leave Type", filters={"jp_statutory_kind": ("in", PERIOD_KINDS)},
		fields=["name", "jp_statutory_kind", "jp_si_exemption"])
	types = {k.name: k for k in kinds if not si_only or k.jp_si_exemption}
	if not types:
		return []
	rows = frappe.get_all("Leave Application", filters={
		"employee": employee, "leave_type": ("in", list(types)), "docstatus": 1, "status": "Approved",
		"from_date": ("<=", to_date), "to_date": (">=", from_date)},
		fields=["name", "leave_type", "from_date", "to_date", "jp_planned_from_date", "jp_planned_to_date"],
		order_by="from_date asc")
	for r in rows:
		r.statutory_kind = types[r.leave_type].jp_statutory_kind
		r.si_exemption = types[r.leave_type].jp_si_exemption
	return rows


# ------------------------------------------------------------------ Employee（勤続通算起算日）
SI_DATE_FIELDS = ("jp_hi_acquired", "jp_pi_acquired", "jp_ei_acquired")


def employee_before_save(doc, method=None):
	"""雇入れ日・勤続通算起算日の変更と、起算日に資格取得日を入れたときに警告する（LVR-06）。"""
	warnings = []
	start = doc.get("jp_service_start_date")
	if start:
		same = [f for f in SI_DATE_FIELDS if doc.get(f) and getdate(doc.get(f)) == getdate(start)]
		if same:
			warnings.append(_("勤続通算起算日に社会保険・雇用保険の資格取得日と同じ日付が入っています。勤続の起算日は資格取得日とは別に定めます。"))
	if not doc.is_new() and (doc.has_value_changed("date_of_joining") or doc.has_value_changed("jp_service_start_date")):
		affected = frappe.get_all(GRANT, filters={"employee": doc.name, "docstatus": 1},
			fields=["name", "grant_date", "days", "status"], order_by="grant_date asc")
		if affected:
			lines = ", ".join(f"{g.name}（{g.grant_date}・{g.days}日・{g.status}）" for g in affected)
			warnings.append(_("雇入れ日・勤続通算起算日を変更しました。付与済みの年休に影響する可能性があります：{0}。必要に応じて取消・再計算してください（LVR-06・25）。").format(lines))
	if warnings:
		frappe.msgprint("<br>".join(warnings), title=_("年次有給休暇への影響"), indicator="orange")
		doc.flags.jp_leave_warnings = warnings
		if not doc.is_new():
			doc.add_comment("Comment", "\n".join(warnings))
