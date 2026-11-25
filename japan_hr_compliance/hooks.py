app_name = "japan_hr_compliance"
app_title = "Japan HR Compliance"
app_publisher = "Lifegence Corporation"
app_description = (
    "Japanese HR, payroll, social insurance, annual leave and withholding tax "
    "compliance for Frappe HR"
)
app_email = "contact@lifegence.com"
app_license = "GPL-3.0-or-later"

# Frappe HR（hrms）の Employee / Salary Slip / Leave Allocation 等を拡張するため必須。
# 和暦・都道府県・法人番号など日本共通の部品は japan_compliance の JP Core を使う。
# required_apps は Frappe 本体がアプリ名で、Press と bench が GitHub のリポジトリ名で照合する。
# リポジトリ名をアプリ名と同じ japan_compliance にしてあるので、両方とも一致する。
required_apps = ["frappe/hrms", "lifegence/japan_compliance"]

# --- 依存の向きに関する原則 -------------------------------------------------
# japan_hr_compliance → japan_compliance の向きのみ可。逆向きの依存を追加しないこと。
# 特定企業の固有アプリには依存しない。詳細は docs/ARCHITECTURE.md を参照。
# ---------------------------------------------------------------------------

# 料率（社会保険・雇用保険）・税額表は適用期間つきのマスタで持ち、コードに書かない。

fixtures = []

before_install = "japan_hr_compliance.install.before_install"
after_install = "japan_hr_compliance.install.after_install"
before_migrate = "japan_hr_compliance.install.before_migrate"
after_migrate = "japan_hr_compliance.install.after_migrate"

# Employee（ERPNext／hrms）は改変せず doc_events で拡張する（従業員マスタ要件 B-1）
doc_events = {
    "Employee": {
        "validate": "japan_hr_compliance.jp_hr_core.employee.validate",
        "on_update": "japan_hr_compliance.jp_hr_core.employee.on_update",
        "on_trash": "japan_hr_compliance.jp_hr_core.employee.on_trash",
    },
}

scheduler_events = {
    "daily": [
        "japan_hr_compliance.jp_hr_core.contract.sync_contracts_effective_today",
        "japan_hr_compliance.jp_hr_core.contract.notify_fixed_term_ending",
    ],
}

# --- 社会保険・雇用保険（B-5 社会保険・雇用保険の計算規則 v1.0） ----------------
# Salary Slip は継承クラスで社保・雇保の控除行と事業主負担行を書き込む（hrms 本体は改変しない）。
# 給与計算の規則（B-4）の拡張も同じクラスに加える想定。
override_doctype_class = {
    **globals().get("override_doctype_class", {}),
    "Salary Slip": "japan_hr_compliance.jp_social_insurance.salary_slip.JPSalarySlip",
}
doc_events.setdefault("JP Employment Contract", {})["on_submit"] = (
    "japan_hr_compliance.jp_social_insurance.eligibility.on_contract_submit"
)
scheduler_events.setdefault("daily", []).append(
    "japan_hr_compliance.jp_social_insurance.eligibility.notify_upcoming_rule_changes"
)
scheduler_events.setdefault("monthly", []).append(
    "japan_hr_compliance.jp_social_insurance.eligibility.monthly_headcount"
)

# --- JP Leave（年次有給休暇 B-3）。既存の行は変えず、ここから追記する ---
after_install = [after_install, "japan_hr_compliance.jp_leave.setup.after_migrate"]
after_migrate = [after_migrate, "japan_hr_compliance.jp_leave.setup.after_migrate"]
doc_events.setdefault("Leave Application", {}).update({
	"validate": [
		"japan_hr_compliance.jp_leave.consumption.validate",
		"japan_hr_compliance.jp_leave.leave_rules.validate_leave_application",
	],
	"on_submit": "japan_hr_compliance.jp_leave.consumption.on_submit",
	"on_cancel": "japan_hr_compliance.jp_leave.consumption.on_cancel",
})
doc_events.setdefault("Leave Type", {}).update({
	"validate": "japan_hr_compliance.jp_leave.leave_rules.validate_leave_type",
	"on_update": "japan_hr_compliance.jp_leave.leave_rules.on_update_leave_type",
})
doc_events.setdefault("Leave Allocation", {}).update({
	"before_cancel": "japan_hr_compliance.jp_leave.container.protect_container",
	"before_update_after_submit": "japan_hr_compliance.jp_leave.container.protect_container",
})
doc_events.setdefault("Employee", {})["before_save"] = "japan_hr_compliance.jp_leave.leave_rules.employee_before_save"
doc_events.setdefault("Company", {})["after_insert"] = "japan_hr_compliance.jp_leave.setup.on_company_insert"
scheduler_events.setdefault("daily", []).append("japan_hr_compliance.jp_leave.jobs.daily")

# --- 従業員マスタ B-1 の P1（追記のみ） ------------------------------------------------------
doc_events["Salary Slip"] = {"validate": "japan_hr_compliance.jp_hr_core.employee.set_salary_slip_display_name"}  # EMP-02

scheduler_events["daily"] += [
	"japan_hr_compliance.jp_hr_core.employee.notify_residence_expiry",  # EMP-04
]

# 標準の frappe.client.get_password は System Manager が Password 型を読めるため、マイナンバーだけ拒否する（EMP-63）
override_whitelisted_methods = {
	"frappe.client.get_password": "japan_hr_compliance.jp_hr_core.my_number.guarded_get_password",
}

# 労働条件通知書の雛形から明示事項の一覧を呼ぶ（EMP-50）
jinja = {"methods": [
	"japan_hr_compliance.jp_hr_core.labor_notice.jp_labor_notice_items",
	"japan_hr_compliance.utils.print_fonts.jp_print_font_css",  # 帳票の日本語フォント（japan_compliance 同梱）
]}

# 他のモジュールが登録できる拡張点（B-1 P1）
# jp_on_dependent_change：扶養の異動の記録（JP Dependent Change）を受け取る関数。源泉の扶養人数の再計算（B-6）用
# jp_contract_retro_impact：fn(employee, start, end) -> list[str]。契約の遡及変更の影響（EMP-27）
jp_on_dependent_change = []
jp_contract_retro_impact = []

# --- 兼務・発令日（JP Concurrent Post／JP Personnel Order）。既存の行は変えず、ここから追記する ---
scheduler_events.setdefault("daily", []).extend([
    "japan_hr_compliance.jp_hr_core.doctype.jp_personnel_order.jp_personnel_order.apply_due_orders",
    "japan_hr_compliance.jp_hr_core.doctype.jp_concurrent_post.jp_concurrent_post.refresh_statuses",
])
