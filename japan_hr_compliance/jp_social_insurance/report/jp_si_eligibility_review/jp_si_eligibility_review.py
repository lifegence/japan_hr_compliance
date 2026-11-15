"""短時間労働者・雇用保険の適用判定の一覧（SIR-70〜73）。

判定日時点の契約・通常の労働者の条件・判定条件マスタから区分を示し、登録上の資格と異なる者に
印を付ける。判定条件の施行日を判定日にすると、施行日の前月に該当者の一覧を出せる（SIR-72）。
資格の取得・喪失は担当者が確定する（自動変更しない）。
"""

import frappe
from frappe import _

from japan_hr_compliance.jp_social_insurance.eligibility import review


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = review(filters.company, filters.date)
	if filters.only_differs:
		rows = [r for r in rows if r.differs]
	for r in rows:
		r.judged_employment = _("適用") if r.judged_employment else (_("適用なし") if r.judged_employment is False else "")
		r.current_employment = _("適用") if r.current_employment else _("適用なし")
	return columns(), rows


def columns():
	return [
		{"fieldname": "employee", "label": _("従業員"), "fieldtype": "Link", "options": "Employee", "width": 120},
		{"fieldname": "employee_name", "label": _("氏名"), "fieldtype": "Data", "width": 120},
		{"fieldname": "judged_social", "label": _("社保（判定）"), "fieldtype": "Data", "width": 110},
		{"fieldname": "current_social", "label": _("社保（登録）"), "fieldtype": "Data", "width": 110},
		{"fieldname": "judged_employment", "label": _("雇保（判定）"), "fieldtype": "Data", "width": 100},
		{"fieldname": "current_employment", "label": _("雇保（登録）"), "fieldtype": "Data", "width": 100},
		{"fieldname": "differs", "label": _("要確認"), "fieldtype": "Check", "width": 70},
		{"fieldname": "reason", "label": _("判定できない理由"), "fieldtype": "Data", "width": 240},
	]
