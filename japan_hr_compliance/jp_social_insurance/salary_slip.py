"""Salary Slip の拡張：社会保険・雇用保険の控除行と事業主負担行の書き込み（SIR-06・20〜33・41・60〜65）。

hrms の Salary Slip は改変せず、override_doctype_class で継承したクラスから
hrms の地域別の差込み口（apply_regional_deductions / apply_regional_employer_contributions）を
上書きする。給与計算の規則（B-4）の拡張も同じクラスに加える想定。

- 社保の控除行・事業主負担行は Salary Component の「社保控除の種別」で識別し、保存のたびに作り直す
- 「社会保険料を手動で変更する」をオンにした明細は作り直さない（金額の手動変更。SIR-26）
- 月額は保険料の対象月ごとの明細行（jp_si_lines）に、使った料率・等級表の版とともに残す（SIR-06）
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate
from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip

from japan_hr_compliance.jp_hr_core.contract import get_contract_at
from japan_hr_compliance.jp_social_insurance import calc
from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import employee_data as ed
from japan_hr_compliance.jp_social_insurance import premium as pm

WAGE_TYPE_TO_CALC = {"月給": calc.PAY_MONTHLY, "日給": calc.PAY_DAILY, "時給": calc.PAY_HOURLY}


class JPSalarySlip(SalarySlip):
	def apply_regional_deductions(self):
		super().apply_regional_deductions()
		apply_social_insurance(self)

	def apply_regional_employer_contributions(self):
		super().apply_regional_employer_contributions()
		apply_employer_contributions(self)

	def validate(self):
		super().validate()
		set_payment_base_days(self)
		if self.docstatus == 1:
			validate_deferred_collection(self)

	def on_submit(self):
		super().on_submit()
		from japan_hr_compliance.jp_social_insurance.bonus import update_accumulation

		update_accumulation(self)

	def on_cancel(self):
		super().on_cancel()
		from japan_hr_compliance.jp_social_insurance.bonus import update_accumulation

		update_accumulation(self, cancel=True)


# ---------------------------------------------------------------------------
# 控除行・事業主負担行
# ---------------------------------------------------------------------------


def _local_cache(key: str) -> dict:
	cache = getattr(frappe.local, key, None)
	if cache is None:
		cache = {}
		setattr(frappe.local, key, cache)
	return cache


def component_for_kind(kind: str) -> frappe._dict:
	cache = _local_cache("jp_si_component_cache")
	if kind not in cache:
		row = frappe.db.get_value("Salary Component", {"jp_si_component_kind": kind, "disabled": 0},
			["name", "salary_component_abbr"], as_dict=True)
		if not row:
			frappe.throw(_("社保控除の種別「{0}」の給与項目（Salary Component）がありません。").format(kind),
				title=_("社会保険の計算"))
		cache[kind] = row
	return cache[kind]


def _si_kind(component: str) -> str | None:
	cache = _local_cache("jp_si_kind_cache")
	if component not in cache:
		cache[component] = frappe.db.get_value("Salary Component", component, "jp_si_component_kind") or None
	return cache[component]


def _replace_rows(slip, parentfield: str, kinds, amounts: dict):
	kept = [r for r in slip.get(parentfield) if _si_kind(r.salary_component) not in kinds]
	slip.set(parentfield, kept)
	for kind in kinds:
		amount = flt(amounts.get(kind, 0))
		if not amount:
			continue
		comp = component_for_kind(kind)
		slip.append(parentfield, {"salary_component": comp.name, "abbr": comp.salary_component_abbr, "amount": amount,
			"default_amount": amount, "additional_amount": 0, "depends_on_payment_days": 0, "do_not_include_in_total": 0})


def apply_social_insurance(slip):
	"""社保・雇保の本人負担を控除行に書き込み、明細行を作る（calculate_net_pay の途中で呼ばれる）。"""
	if not slip.employee or not slip.posting_date or not slip.start_date:
		return
	if cint(slip.get("jp_si_manual")):
		slip._jp_si_result = None
		return
	result = pm.compute_for_slip(slip)
	slip._jp_si_result = result
	_replace_rows(slip, "deductions", C.DEDUCTION_KINDS, result.deductions)
	slip.set("jp_si_lines", result.lines)
	slip.jp_si_target_months = ", ".join(f"{m.year}-{m.month:02d}" for m in result.target_months)
	slip.jp_si_deferred_months = ", ".join(f"{m.year}-{m.month:02d}" for m in result.deferred_months)
	slip.jp_si_warnings = "\n".join(result.warnings)
	bp = result.bonus
	slip.jp_bonus_standard = float(bp.standard_bonus) if bp else 0
	slip.jp_bonus_health_standard = float(bp.health_standard_bonus) if bp else 0
	slip.jp_bonus_pension_standard = float(bp.pension_standard_bonus) if bp else 0
	slip.jp_bonus_health_treatment = C.BONUS_TREATMENT_LABELS.get(bp.health_treatment) if bp else None
	slip.jp_bonus_report_required = 1 if bp and bp.report_required else 0


def apply_employer_contributions(slip):
	result = getattr(slip, "_jp_si_result", None)
	if result is None:
		return
	_replace_rows(slip, "employer_contributions", C.EMPLOYER_KINDS, result.employer)


def validate_deferred_collection(slip):
	"""退職時の2か月控除を取り消した場合の徴収方法の記録（SIR-26）。"""
	if slip.get("jp_si_deferred_months") and not slip.get("jp_si_deferred_method"):
		frappe.throw(_("退職時の2か月控除を取り消した対象月（{0}）の徴収方法（別途振込／翌月以降の支給から控除）を選んでください。")
			.format(slip.jp_si_deferred_months), title=_("徴収方法"))


# ---------------------------------------------------------------------------
# 支払基礎日数と計算期間の欠勤日数（SIR-41）
# ---------------------------------------------------------------------------


def absence_days_in_period(employee, start, end) -> float:
	"""計算期間の欠勤日数（控除月によらず、その期間の勤怠から数える）。"""
	rows = frappe.get_all("Attendance", filters={"employee": employee, "docstatus": 1,
		"attendance_date": ("between", [start, end])}, fields=["status", "leave_type"])
	lwp_types = set(frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name"))
	days = 0.0
	for r in rows:
		if r.status == "Absent":
			days += 1
		elif r.status == "Half Day" and (not r.leave_type or r.leave_type in lwp_types):
			days += 0.5
		elif r.status == "On Leave" and r.leave_type in lwp_types:
			days += 1
	return days


def attendance_days_in_period(employee, start, end) -> float | None:
	"""出勤日数（有給休暇を含む）。勤怠の記録がなければ None。"""
	rows = frappe.get_all("Attendance", filters={"employee": employee, "docstatus": 1,
		"attendance_date": ("between", [start, end])}, fields=["status", "leave_type"])
	if not rows:
		return None
	lwp_types = set(frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name"))
	days = 0.0
	for r in rows:
		if r.status in ("Present", "Work From Home", "Half Day"):
			days += 1
		elif r.status == "On Leave" and r.leave_type not in lwp_types:
			days += 1
	return days


def set_payment_base_days(slip):
	if not slip.employee or not slip.start_date or not slip.end_date:
		return
	start, end = getdate(slip.start_date), getdate(slip.end_date)
	if not cint(slip.get("jp_absence_days_manual")):
		slip.jp_absence_days = absence_days_in_period(slip.employee, start, end)
	contract = get_contract_at(slip.employee, end) or get_contract_at(slip.employee, start)
	pay_type = WAGE_TYPE_TO_CALC.get(contract.wage_type if contract else None, calc.PAY_MONTHLY)
	settings = ed.get_settings(slip.company)
	if pay_type == calc.PAY_MONTHLY:
		absence = flt(slip.jp_absence_days)
		if cint(settings.absence_deduction_monthly) and absence:
			prescribed = flt(slip.total_working_days)
			if settings.prescribed_days_basis == C.PRESCRIBED_CONTRACT and contract and contract.monthly_days:
				prescribed = flt(contract.monthly_days)
			slip.jp_payment_base_days = max(0.0, prescribed - absence)
		else:
			slip.jp_payment_base_days = date_diff(end, start) + 1
	else:
		days = attendance_days_in_period(slip.employee, start, end)
		slip.jp_payment_base_days = days if days is not None else flt(slip.payment_days)
