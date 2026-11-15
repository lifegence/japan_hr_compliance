"""短時間労働者・雇用保険の適用判定と、厚年被保険者数の記録・警告（SIR-70〜73）。

- 判定日時点の契約（B-1 get_contract_at）と通常の労働者の所定労働条件（B-1
  get_employee_standard_condition）、施行日つきの判定条件マスタ（JP SI Eligibility Rule）から区分を示す
- 資格の取得・喪失は担当者が確定する。ここでは一覧・通知を出すだけで自動変更しない（SIR-72）
- 特定適用事業所の該当は自動で判定せず、人数の推移から警告し、届出後に担当者が JP SI Settings に登録する
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, today

from japan_hr_compliance.jp_hr_core.contract import get_contract_at
from japan_hr_compliance.jp_hr_core.workplace import get_employee_standard_condition
from japan_hr_compliance.jp_social_insurance import calc, masters
from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import employee_data as ed
from japan_hr_compliance.jp_social_insurance.masters import dec, month_start

OVER_TWO_MONTHS = ("期間の定めなし", "2か月を超える見込み")
UNDER_31_DAYS = "31日未満の見込み"
DAY_STUDENT = "昼間学生"
HEADCOUNT = "JP Company Headcount"


def monthly_wage(employee, on_date) -> float:
	"""所定内賃金の月額の見込み。給与構造の割当の base を使う（PR に記載の仮の扱い）。"""
	return flt(frappe.db.get_value("Salary Structure Assignment",
		{"employee": employee, "docstatus": 1, "from_date": ("<=", getdate(on_date))}, "base", order_by="from_date desc"))


def classify_employee(employee: str, on_date, rules=None) -> frappe._dict:
	"""判定日時点の社保の適用区分（一般／短時間労働者／適用なし）と雇保の適用（SIR-70・71・73）。"""
	on_date = getdate(on_date)
	out = frappe._dict(employee=employee, date=on_date, social=None, employment=None, reason=None)
	contract = get_contract_at(employee, on_date)
	if not contract:
		out.reason = _("判定日の雇用契約がありません")
		return out
	rules = rules if rules is not None else masters.load_eligibility_rules()
	standard = get_employee_standard_condition(employee, on_date)
	company = contract.company or frappe.db.get_value("Employee", employee, "company")
	settings = ed.get_settings(company)
	weekly = dec(flt(contract.weekly_hours))
	monthly_days = flt(contract.monthly_days)
	if not monthly_days and flt(contract.weekly_days):
		monthly_days = flt(contract.weekly_days) * 52 / 12
	try:
		if not standard:
			out.reason = _("通常の労働者の所定労働条件（JP Standard Worker Condition）が登録されていません")
		else:
			coverage = calc.classify_social_insurance(on_date, rules, weekly_hours=weekly,
				monthly_days=dec(round(monthly_days, 4)), normal_weekly_hours=dec(flt(standard.weekly_hours)),
				normal_monthly_days=dec(flt(standard.monthly_days)), monthly_wage=dec(monthly_wage(employee, on_date)),
				expected_over_two_months=contract.expected_period in OVER_TWO_MONTHS,
				is_student=contract.student_type == DAY_STUDENT,
				specific_applicable_office=ed.is_specific_applicable_office(settings, on_date))
			out.social = C.COVERAGE_LABELS[coverage]
		out.employment = calc.classify_employment_insurance(on_date, rules, weekly_hours=weekly,
			expected_31_days_or_more=contract.expected_period != UNDER_31_DAYS,
			excluded_student=contract.student_type == DAY_STUDENT)
		if ed.officer_type(employee) == C.OFFICER:
			out.employment = False
	except LookupError as e:
		out.reason = _("判定条件の確認済の版がありません：{0}").format(e)
	return out


def current_status(employee: str, on_date) -> frappe._dict:
	"""判定日時点の登録上の資格（社保の区分と雇保の有無）。"""
	health = ed.qualification_on(employee, C.INS_HEALTH, on_date)
	pension = ed.qualification_on(employee, C.INS_PENSION, on_date)
	found = pension or health
	return frappe._dict(social=found[1] if found else C.COVERAGE_LABELS[calc.COVERAGE_NONE],
		employment=bool(ed.qualification_on(employee, C.INS_EMPLOYMENT, on_date)))


def review(company: str, on_date) -> list[frappe._dict]:
	"""在籍者の判定と登録上の資格を並べ、異なる者に印を付ける（SIR-72）。"""
	rules = masters.load_eligibility_rules()
	rows = []
	for emp in frappe.get_all("Employee", filters={"company": company, "status": "Active"},
			fields=["name", "employee_name"], order_by="name"):
		judged = classify_employee(emp.name, on_date, rules)
		cur = current_status(emp.name, on_date)
		rows.append(frappe._dict(employee=emp.name, employee_name=emp.employee_name, judged_social=judged.social,
			current_social=cur.social, judged_employment=judged.employment, current_employment=cur.employment,
			differs=bool((judged.social and judged.social != cur.social) or (judged.employment is not None
				and judged.employment != cur.employment)), reason=judged.reason))
	return rows


def on_contract_submit(doc, method=None):
	"""契約の確定時に再判定し、登録上の資格と異なれば人事担当に知らせる（SIR-72。自動変更しない）。"""
	judged = classify_employee(doc.employee, doc.valid_from)
	cur = current_status(doc.employee, doc.valid_from)
	if (judged.social and judged.social != cur.social) or (judged.employment is not None and judged.employment != cur.employment):
		message = _("{0}：契約 {1}（{2} から）の条件では社保「{3}」・雇保「{4}」と判定されます（登録上は社保「{5}」・雇保「{6}」）。資格の取得・喪失を確認してください。").format(
			doc.employee_name or doc.employee, doc.name, doc.valid_from, judged.social or _("判定不可"),
			"適用" if judged.employment else "適用なし", cur.social, "適用" if cur.employment else "適用なし")
		frappe.msgprint(message, title=_("社会保険・雇用保険の適用判定"), indicator="orange")
		_notify(message, "JP Employment Contract", doc.name)


def _notify(subject, doctype, name):
	from japan_hr_compliance.jp_hr_core.contract import hr_users

	for user in hr_users():
		frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert", "document_type": doctype,
			"document_name": name, "subject": subject[:140]}).insert(ignore_permissions=True)


def notify_upcoming_rule_changes():
	"""日次：判定条件の施行日の前月1日に、施行日時点の判定で区分が変わる者を知らせる（SIR-72）。"""
	first = getdate(today())
	if first.day != 1:
		return []
	effective = getdate(add_months(first, 1))
	rules = frappe.get_all("JP SI Eligibility Rule", filters={"status": C.STATUS_CONFIRMED,
		"effective_from": ("between", [effective, getdate(add_months(effective, 1)) - timedelta(days=1)])},
		pluck="name")
	sent = []
	for rule in rules:
		for company in frappe.get_all("Company", pluck="name"):
			changed = [r for r in review(company, frappe.db.get_value("JP SI Eligibility Rule", rule, "effective_from"))
				if r.differs]
			if changed:
				_notify(_("判定条件 {0} の施行で適用区分が変わる従業員が {1} 名います（{2}）。適用判定の一覧を確認してください。")
					.format(rule, len(changed), company), "JP SI Eligibility Rule", rule)
				sent.append((rule, company, len(changed)))
	return sent


# ---------------------------------------------------------------------------
# 厚年被保険者数（SIR-71）
# ---------------------------------------------------------------------------


def count_pension_insured(company: str, month) -> int:
	"""月末時点の厚年被保険者数（短時間労働者を除く）。"""
	end = ed.calc_month_end(month_start(month))
	count = 0
	for emp in frappe.get_all("Employee", filters={"company": company}, pluck="name"):
		found = ed.qualification_on(emp, C.INS_PENSION, end)
		if found and found[1] != C.CATEGORY_SHORT_TIME:
			count += 1
	return count


def record_headcount(company: str, month, count: int | None = None):
	"""会社・年月の厚年被保険者数を記録し、警告を判定する。"""
	month = month_start(month)
	name = frappe.db.get_value(HEADCOUNT, {"company": company, "month": month}, "name")
	doc = frappe.get_doc(HEADCOUNT, name) if name else frappe.get_doc({"doctype": HEADCOUNT, "company": company,
		"month": month})
	if count is not None:
		doc.pension_insured_count = count
	elif not cint(doc.manually_adjusted):
		doc.pension_insured_count = count_pension_insured(company, month)
	threshold = None
	try:
		rule = calc.select_version(masters.load_eligibility_rules(), ed.calc_month_end(month), by_month=False)
		threshold = rule.company_size_threshold
	except LookupError:
		pass
	doc.threshold = threshold
	doc.save(ignore_permissions=True)
	if threshold:
		history = {getdate(r.month): cint(r.pension_insured_count) for r in frappe.get_all(HEADCOUNT,
			filters={"company": company, "month": ("<=", month)}, fields=["month", "pension_insured_count"])}
		warning = calc.headcount_warning(history, threshold, month)
		doc.db_set("warning", 1 if warning else 0)
	return doc


def monthly_headcount():
	"""月次：前月の厚年被保険者数を記録し、特定適用事業所でない会社に警告を出す。"""
	month = month_start(add_months(today(), -1))
	out = []
	for company in frappe.get_all("JP SI Settings", pluck="company"):
		doc = record_headcount(company, month)
		settings = ed.get_settings(company)
		if cint(doc.warning) and not cint(settings.specific_applicable_office):
			_notify(_("{0}：厚年被保険者数が企業規模要件（{1}人）以上の月が続いています。特定適用事業所の該当を確認し、届出後に設定へ登録してください。")
				.format(company, doc.threshold), HEADCOUNT, doc.name)
		out.append(doc.name)
	return out
