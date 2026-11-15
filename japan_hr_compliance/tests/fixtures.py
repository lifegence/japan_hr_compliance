"""テスト用のマスタと従業員。名称・番号はすべて架空。何度呼んでもよい（存在するものは作らない）。"""

import frappe

COMPANY = "テスト人事A株式会社"
ABBR = "THA"

WORKPLACE_HQ = "テスト本社事業場"
WORKPLACE_FACTORY = "テスト工場事業場"
JOB_OFFICE = "テスト一般事務"
JOB_SALES = "テスト営業"
JOB_INSPECT = "テスト製品検査"


def ensure_all():
	ensure_company()
	ensure_genders()
	ensure_job(JOB_OFFICE, "一般事務", "03 事務的職業")
	ensure_job(JOB_SALES, "営業", "04 販売の職業")
	ensure_job(JOB_INSPECT, "製品検査", "08 生産工程の職業")
	ensure_workplace(WORKPLACE_HQ, branch="テスト本社")
	ensure_workplace(WORKPLACE_FACTORY, branch="テスト工場")


def ensure_company():
	from japan_compliance.tests import fixtures as jc

	jc.ensure_erpnext_fixtures()
	jc.ensure_jpy_defaults()
	jc.ensure_company(COMPANY, ABBR)


def ensure_genders():
	for gender in ("Male", "Female"):
		if not frappe.db.exists("Gender", gender):
			frappe.get_doc({"doctype": "Gender", "gender": gender}).insert(ignore_permissions=True)


def ensure_job(name, label36, ei_class):
	if not frappe.db.exists("JP Job Category", name):
		frappe.get_doc({"doctype": "JP Job Category", "job_name": name, "agreement36_label": label36,
			"ei_job_class": ei_class}).insert(ignore_permissions=True)


def ensure_branch(name):
	if not frappe.db.exists("Branch", name):
		frappe.get_doc({"doctype": "Branch", "branch": name}).insert(ignore_permissions=True)


def ensure_workplace(name, branch=None, units=()):
	if frappe.db.exists("JP Workplace", name):
		return frappe.get_doc("JP Workplace", name)
	if branch:
		ensure_branch(branch)
	doc = frappe.get_doc({"doctype": "JP Workplace", "workplace_name": name, "company": COMPANY, "branch": branch,
		"address": "東京都豊島区（架空）", "labour_office": "テスト労働基準監督署", "insurance_units": list(units)})
	doc.insert(ignore_permissions=True)
	return doc


_seq = [0]


def make_employee(joining="2025-04-01", first_name=None, **kw):
	_seq[0] += 1
	doc = frappe.get_doc({
		"doctype": "Employee", "first_name": first_name or f"太郎{frappe.generate_hash(length=6)}", "last_name": "試験",
		"jp_last_name_kana": "シケン", "jp_first_name_kana": "タロウ", "gender": "Male", "date_of_birth": "1990-05-10",
		"date_of_joining": joining, "company": COMPANY, "status": "Active", **kw,
	})
	doc.insert(ignore_permissions=True)
	return doc


def make_contract(employee, valid_from, submit=True, **kw):
	values = {
		"doctype": "JP Employment Contract", "employee": employee, "valid_from": valid_from,
		"employment_class": "正社員", "work_rule_class": "正社員用", "wage_type": "月給", "working_time_system": "通常",
		"expected_period": "期間の定めなし", "student_type": "学生でない",
		"weekly_hours_h": 40, "weekly_days": 5, "daily_hours_h": 8, "monthly_days": 21,
		"workplace": WORKPLACE_HQ, "job_category": JOB_OFFICE,
	}
	if frappe.db.exists("JP Employment Contract", {"employee": employee, "docstatus": 1, "valid_from": ("<", valid_from)}):
		values["change_reason"] = "条件変更"  # 2件目以降は変更理由が必須（EMP-27）
	values.update(kw)
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def ensure_user(email, roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0,
			"roles": [{"role": r} for r in roles]}).insert(ignore_permissions=True)
	return email
