"""JP Leave の初期設定（Custom Field・法定休暇の休暇種別・法定の付与テーブル・会社設定）。

hrms 標準 DocType は改変せず、Custom Field で拡張する（B-3 6章）。
- Leave Application：取得の区分（LVR-41）、半日の午前／午後（LVR-40）、法定休業の予定期間（LVR-73）
- Leave Type：休暇の分類・出勤率での扱い・社保免除の判定対象・根拠条文（LVR-13・70〜74）
- Attendance：全労働日から除く日・みなし出勤の区分（LVR-11。B-2 の日次の勤怠区分ができるまでの仮置き）
- Employee：勤続通算起算日（LVR-06）、介護休暇の対象家族の人数（LVR-72）
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from japan_hr_compliance.jp_leave.calc.grant_table import LEGAL_DAYS, LEGAL_SERVICE_MONTHS
from japan_hr_compliance.jp_leave.utils import (
	ANNUAL_LEAVE_TYPE,
	CATEGORY_ANNUAL,
	CATEGORY_SPECIAL,
	CATEGORY_STATUTORY,
	GRANT_TABLE,
	SETTINGS,
)

LEGAL_TABLE_NAME = "法定（通常・比例付与）"

TREAT_DEEMED, TREAT_EXCLUDED, TREAT_ABSENT = "出勤とみなす", "全労働日から除く", "欠勤"

# 法定休暇の区分。DayStatus への対応は attendance_source で行う
SK_MATERNITY = "産前産後休業"
SK_CHILDCARE = "育児休業"
SK_PATERNITY = "出生時育児休業"
SK_FAMILY_CARE = "介護休業"
SK_INJURY = "業務上の傷病による休業"
SK_NURSING = "子の看護等休暇"
SK_CARE_DAY = "介護休暇"
SK_MENSTRUAL = "生理休暇"
SK_CHILDCARE_TIME = "育児時間"
STATUTORY_KINDS = (SK_MATERNITY, SK_CHILDCARE, SK_PATERNITY, SK_FAMILY_CARE, SK_INJURY, SK_NURSING, SK_CARE_DAY,
	SK_MENSTRUAL, SK_CHILDCARE_TIME)
#: 39条10項により出勤したものとみなす休業（扱いは変更できない）
LEGALLY_DEEMED_KINDS = (SK_MATERNITY, SK_CHILDCARE, SK_PATERNITY, SK_FAMILY_CARE, SK_INJURY)
#: 開始日・終了日（予定と実績）を期間として記録する休業（LVR-73）
PERIOD_KINDS = (SK_MATERNITY, SK_CHILDCARE, SK_PATERNITY, SK_FAMILY_CARE)

# (名称, 分類, 法定休暇の区分, 無給, 出勤率での扱い, 社保免除の判定対象, 根拠)
LEAVE_TYPES = (
	(ANNUAL_LEAVE_TYPE, CATEGORY_ANNUAL, "", 0, TREAT_DEEMED, 0, "労基法39条"),
	(SK_MATERNITY, CATEGORY_STATUTORY, SK_MATERNITY, 1, TREAT_DEEMED, 1, "労基法65条"),
	(SK_CHILDCARE, CATEGORY_STATUTORY, SK_CHILDCARE, 1, TREAT_DEEMED, 1, "育介法5条"),
	(SK_PATERNITY, CATEGORY_STATUTORY, SK_PATERNITY, 1, TREAT_DEEMED, 1, "育介法9条の2"),
	(SK_FAMILY_CARE, CATEGORY_STATUTORY, SK_FAMILY_CARE, 1, TREAT_DEEMED, 0, "育介法11条"),
	(SK_NURSING, CATEGORY_STATUTORY, SK_NURSING, 1, TREAT_DEEMED, 0, "育介法16条の2"),
	(SK_CARE_DAY, CATEGORY_STATUTORY, SK_CARE_DAY, 1, TREAT_DEEMED, 0, "育介法16条の5"),
	(SK_MENSTRUAL, CATEGORY_STATUTORY, SK_MENSTRUAL, 1, TREAT_DEEMED, 0, "労基法68条"),
	(SK_CHILDCARE_TIME, CATEGORY_STATUTORY, SK_CHILDCARE_TIME, 1, TREAT_DEEMED, 0, "労基法67条"),
	("業務災害による休業", CATEGORY_STATUTORY, SK_INJURY, 1, TREAT_DEEMED, 0, "労基法39条10項・76条"),
)

TAKE_KINDS = "本人の請求\n計画的付与\n時季指定"
DAY_CLASSES = (
	"\n不可抗力による休業\n使用者側に起因する経営・管理上の障害による休業\n正当な争議行為"
	"\n労働者の責に帰すべき事由によらない不就労"
)

CUSTOM_FIELDS = {
	"Leave Application": [
		dict(fieldname="jp_take_kind", label="取得の区分（年休）", fieldtype="Select", options=TAKE_KINDS,
			default="本人の請求", insert_after="leave_type", description="年5日の取得日数に数える区分（LVR-41）"),
		dict(fieldname="jp_half_day_part", label="半日の区分", fieldtype="Select", options="\n午前\n午後",
			insert_after="half_day_date", depends_on="half_day", description="LVR-40"),
		dict(fieldname="jp_period_section", label="法定休業の予定期間", fieldtype="Section Break",
			insert_after="leave_balance", collapsible=1,
			description="産前産後休業・育児休業・介護休業は予定と実績（申請の期間）を記録する（LVR-73）"),
		dict(fieldname="jp_planned_from_date", label="予定の開始日", fieldtype="Date", insert_after="jp_period_section"),
		dict(fieldname="jp_planned_to_date", label="予定の終了日", fieldtype="Date", insert_after="jp_planned_from_date"),
	],
	"Leave Type": [
		dict(fieldname="jp_section", label="日本の休暇の設定", fieldtype="Section Break", insert_after="is_optional_leave"),
		dict(fieldname="jp_leave_category", label="休暇の分類", fieldtype="Select",
			options=f"\n{CATEGORY_ANNUAL}\n{CATEGORY_STATUTORY}\n{CATEGORY_SPECIAL}", insert_after="jp_section",
			description="特別休暇は年休の残日数・年5日に影響しない（LVR-74）"),
		dict(fieldname="jp_statutory_kind", label="法定休暇の区分", fieldtype="Select",
			options="\n" + "\n".join(STATUTORY_KINDS), insert_after="jp_leave_category",
			depends_on=f"eval:doc.jp_leave_category=='{CATEGORY_STATUTORY}'"),
		dict(fieldname="jp_statutory_basis", label="根拠条文", fieldtype="Data", insert_after="jp_statutory_kind"),
		dict(fieldname="jp_cb1", fieldtype="Column Break", insert_after="jp_statutory_basis"),
		dict(fieldname="jp_attendance_treatment", label="出勤率の算定での扱い", fieldtype="Select",
			options=f"{TREAT_DEEMED}\n{TREAT_EXCLUDED}\n{TREAT_ABSENT}", default=TREAT_DEEMED, insert_after="jp_cb1",
			description="LVR-13。会社が承認・付与する休暇を「欠勤」にすると不利益取扱いのおそれがある"),
		dict(fieldname="jp_si_exemption", label="社会保険料免除の判定に使う（SI-008）", fieldtype="Check",
			insert_after="jp_attendance_treatment"),
	],
	"Attendance": [
		dict(fieldname="jp_day_class", label="出勤率の算定上の区分", fieldtype="Select", options=DAY_CLASSES,
			insert_after="half_day_status",
			description="全労働日から除く日・出勤日として扱う不就労日（B-3 LVR-11）。空欄は勤怠の状態で判定する"),
	],
	"Leave Allocation": [
		dict(fieldname="jp_leave_container", label="年次有給休暇の器（JP）", fieldtype="Check", read_only=1,
			insert_after="leave_type", no_copy=1,
			description="従業員ごとに長期の1本。付与・失効は japan_hr_compliance が台帳行で増減する（B-3 3.1）"),
	],
	"Employee": [
		dict(fieldname="jp_leave_section", label="年次有給休暇・休暇", fieldtype="Section Break",
			insert_after="jp_death_cause"),
		dict(fieldname="jp_service_start_date", label="勤続通算起算日", fieldtype="Date", insert_after="jp_leave_section",
			description="空欄は入社日。契約更新・転換・再雇用・在籍出向で通算する場合に入力（LVR-06）。社保・雇保の資格取得日は使わない"),
		dict(fieldname="jp_service_start_confirmed", label="勤続通算は確定", fieldtype="Check", default="1",
			insert_after="jp_service_start_date", description="未確定の間は付与予定を確認待ちにする（LVR-20）"),
		dict(fieldname="jp_leave_cb1", fieldtype="Column Break", insert_after="jp_service_start_confirmed"),
		dict(fieldname="jp_care_family_count", label="介護休暇の対象家族の人数", fieldtype="Int",
			insert_after="jp_leave_cb1", description="介護休暇の上限（1人5日・2人以上10日）に使う（LVR-72）"),
	],
}


def after_migrate():
	create_custom_fields(CUSTOM_FIELDS, update=True)
	ensure_leave_types()
	ensure_legal_grant_table()
	for company in frappe.get_all("Company", pluck="name"):
		ensure_settings(company)


def ensure_leave_types():
	"""法定休暇の休暇種別を初期データとして用意する（LVR-70・71）。既存の種別は空の項目だけ埋める。"""
	for name, category, kind, lwp, treatment, si, basis in LEAVE_TYPES:
		if frappe.db.exists("Leave Type", name):
			doc = frappe.get_doc("Leave Type", name)
			changed = False
			for field, value in (("jp_leave_category", category), ("jp_statutory_kind", kind), ("jp_statutory_basis", basis)):
				if value and not doc.get(field):
					doc.set(field, value)
					changed = True
			if changed:
				doc.flags.ignore_permissions = True
				doc.save()
			continue
		doc = frappe.get_doc({
			"doctype": "Leave Type", "leave_type_name": name, "is_lwp": lwp, "include_holiday": 0,
			# 年休は hrms の残高チェックを警告にとどめ、付与単位の残高検査（consumption.validate）で止める（3.1 の検証結果）
			"allow_negative": 1 if category == CATEGORY_ANNUAL else 0,
			"jp_leave_category": category, "jp_statutory_kind": kind, "jp_attendance_treatment": treatment,
			"jp_si_exemption": si, "jp_statutory_basis": basis,
		})
		doc.flags.ignore_permissions = True
		doc.insert()


def legal_rows():
	return [
		{"service_months": m, "category": cat.value, "days": d}
		for cat, days in LEGAL_DAYS.items()
		for m, d in zip(LEGAL_SERVICE_MONTHS, days)
	]


def ensure_legal_grant_table():
	"""法定の表（規則 2.1・2.2）を初期データとして用意する（LVR-02）。"""
	if frappe.db.exists(GRANT_TABLE, LEGAL_TABLE_NAME):
		return
	frappe.get_doc({
		"doctype": GRANT_TABLE, "table_name": LEGAL_TABLE_NAME, "is_legal_default": 1, "rows": legal_rows(),
	}).insert(ignore_permissions=True)


def ensure_settings(company):
	if frappe.db.exists(SETTINGS, company) or not frappe.db.exists("Leave Type", ANNUAL_LEAVE_TYPE):
		return
	frappe.get_doc({"doctype": SETTINGS, "company": company, "annual_leave_type": ANNUAL_LEAVE_TYPE}).insert(
		ignore_permissions=True
	)


def on_company_insert(doc, method=None):
	"""新規会社の作成時に年休の会社設定を作る（法定休暇の種別は全社共通で作成済み。LVR-70）。"""
	ensure_settings(doc.name)
