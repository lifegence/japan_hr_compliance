"""従業員ポータルの同意・承諾の画面（従業員マスタ要件 B-1 v1.0 EMP-36）。

4項目を1画面で示し、項目ごとに「同意する／同意しない（撤回する）」を個別に選ぶ。一括同意のボタンは置かない。
"""

import frappe
from frappe import _

from japan_hr_compliance.jp_hr_core import consent

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("ログインしてください。"), frappe.PermissionError)
	employee = consent.employee_for_session()
	status = consent.get_consent_status(employee)
	context.title = _("同意・承諾の確認")
	context.employee = employee
	context.employee_name = frappe.db.get_value("Employee", employee, "employee_name")
	context.items = [
		frappe._dict(consent_type=t, version=consent.CONSENT_TEXTS[t][0], text=consent.CONSENT_TEXTS[t][1], status=status[t])
		for t in consent.CONSENT_TYPES
	]
	context.bank_transfer = consent.BANK_TRANSFER
	context.accounts = consent.accounts_snapshot(employee)
	return context
