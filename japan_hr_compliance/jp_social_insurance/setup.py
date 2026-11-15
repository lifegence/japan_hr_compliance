"""社会保険・雇用保険（B-5）の Custom Field と既定の給与項目（計算規則 v1.0 7章）。

hrms・ERPNext の標準 DocType は改変せず、Custom Field で項目を加える。
料率・等級の値は作らない（マスタは利用者が CSV 等で登録する）。
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from japan_hr_compliance.jp_social_insurance import constants as C


def _opts(values, blank=True):
	return ("\n" if blank else "") + "\n".join(values)


SALARY_COMPONENT_FIELDS = [
	dict(fieldname="jp_si_section", label="社会保険・雇用保険（日本）", fieldtype="Section Break", insert_after="disabled",
		collapsible=1),
	dict(fieldname="jp_si_component_kind", label="社保控除の種別", fieldtype="Select", options=_opts(C.COMPONENT_KINDS),
		insert_after="jp_si_section", description="社会保険料・雇用保険料の控除行・事業主負担行に使う項目。設定した項目は計算で上書きされる"),
	dict(fieldname="jp_si_pay_class", label="社保の報酬区分", fieldtype="Select",
		options=_opts((C.PAY_CLASS_REMUNERATION, C.PAY_CLASS_BONUS, C.PAY_CLASS_EXCLUDED), blank=False),
		default=C.PAY_CLASS_REMUNERATION, insert_after="jp_si_component_kind",
		description="報酬＝標準報酬月額の算定対象。賞与＝標準賞与額の対象。対象外＝退職金・慶弔見舞金等"),
	dict(fieldname="jp_annual_payments", label="賃金規程上の年間支給回数", fieldtype="Int", insert_after="jp_si_pay_class",
		depends_on=f"eval:doc.jp_si_pay_class=='{C.PAY_CLASS_BONUS}'",
		description="4回以上なら報酬として扱う。規程で回数が明らかでない場合は 0（実績で警告。SIR-33）"),
	dict(fieldname="jp_si_cb1", fieldtype="Column Break", insert_after="jp_annual_payments"),
	dict(fieldname="jp_fixed_wage", label="固定的賃金区分", fieldtype="Select", options=_opts((C.FIXED, C.NON_FIXED), blank=False),
		default=C.NON_FIXED, insert_after="jp_si_cb1", description="随時改定の候補の抽出に使う（SIR-44）"),
	dict(fieldname="jp_in_kind", label="現物給与", fieldtype="Check", insert_after="jp_fixed_wage"),
	dict(fieldname="jp_retroactive", label="遡及差額", fieldtype="Check", insert_after="jp_in_kind",
		description="定時決定・随時改定の平均から除く（PY-012）"),
	dict(fieldname="jp_ei_target", label="雇用保険・労災の対象賃金区分", fieldtype="Select",
		options=_opts((C.EI_TARGET, C.EI_EXCLUDED), blank=False), default=C.EI_TARGET, insert_after="jp_retroactive",
		description="通勤手当は対象、退職金・慶弔見舞金・出張旅費等は対象外（SIR-62）"),
]

SALARY_SLIP_FIELDS = [
	dict(fieldname="jp_si_section", label="社会保険・雇用保険", fieldtype="Section Break", insert_after="employer_contributions",
		collapsible=1),
	dict(fieldname="jp_is_bonus", label="賞与明細", fieldtype="Check", insert_after="jp_si_section",
		description="賞与だけの明細。月額の社会保険料を計算しない"),
	dict(fieldname="jp_si_closing_date", label="賃金締切日", fieldtype="Date", insert_after="jp_is_bonus",
		description="空欄は計算期間の終了日。雇用保険料率の切替に使う（SIR-60）"),
	dict(fieldname="jp_payment_base_days", label="支払基礎日数", fieldtype="Float", read_only=1, insert_after="jp_si_closing_date"),
	dict(fieldname="jp_absence_days", label="計算期間の欠勤日数", fieldtype="Float", insert_after="jp_payment_base_days",
		description="控除月によらず、この計算期間の欠勤日数（SIR-41）"),
	dict(fieldname="jp_absence_days_manual", label="欠勤日数を手入力する", fieldtype="Check", insert_after="jp_absence_days"),
	dict(fieldname="jp_si_cb1", fieldtype="Column Break", insert_after="jp_absence_days_manual"),
	dict(fieldname="jp_si_target_months", label="控除した保険料の対象月", fieldtype="Data", read_only=1, insert_after="jp_si_cb1"),
	dict(fieldname="jp_si_cancel_two_months", label="退職時の2か月控除を取り消す", fieldtype="Check",
		insert_after="jp_si_target_months", description="翌月控除の月末退職者の最終給与（SIR-26）"),
	dict(fieldname="jp_si_deferred_months", label="控除しなかった対象月", fieldtype="Data", read_only=1,
		insert_after="jp_si_cancel_two_months"),
	dict(fieldname="jp_si_deferred_method", label="控除しなかった月の徴収方法", fieldtype="Select",
		options=_opts((C.DEFERRED_TRANSFER, C.DEFERRED_LATER_PAY)), insert_after="jp_si_deferred_months",
		depends_on="jp_si_deferred_months"),
	dict(fieldname="jp_si_manual", label="社会保険料を手動で変更する（再計算しない）", fieldtype="Check",
		insert_after="jp_si_deferred_method", description="控除行・事業主負担行の金額を直接修正する場合"),
	dict(fieldname="jp_si_warnings", label="社会保険の確認事項", fieldtype="Small Text", read_only=1, insert_after="jp_si_manual"),
	dict(fieldname="jp_si_lines_section", label="保険料の明細（対象月ごと）", fieldtype="Section Break",
		insert_after="jp_si_warnings", collapsible=1),
	dict(fieldname="jp_si_lines", label="保険料の明細", fieldtype="Table", options="JP SI Premium Line", read_only=1,
		insert_after="jp_si_lines_section"),
	dict(fieldname="jp_bonus_section", label="賞与の標準賞与額", fieldtype="Section Break", insert_after="jp_si_lines",
		collapsible=1, depends_on="jp_bonus_standard"),
	dict(fieldname="jp_bonus_standard", label="標準賞与額", fieldtype="Currency", options="currency", read_only=1,
		insert_after="jp_bonus_section"),
	dict(fieldname="jp_bonus_health_standard", label="健保の標準賞与額", fieldtype="Currency", options="currency",
		read_only=1, insert_after="jp_bonus_standard"),
	dict(fieldname="jp_bonus_pension_standard", label="厚年の標準賞与額", fieldtype="Currency", options="currency",
		read_only=1, insert_after="jp_bonus_health_standard"),
	dict(fieldname="jp_bonus_cb1", fieldtype="Column Break", insert_after="jp_bonus_pension_standard"),
	dict(fieldname="jp_bonus_health_treatment", label="賞与の扱い", fieldtype="Data", read_only=1, insert_after="jp_bonus_cb1"),
	dict(fieldname="jp_bonus_report_required", label="賞与支払届の対象", fieldtype="Check", read_only=1,
		insert_after="jp_bonus_health_treatment"),
]

LEAVE_TYPE_FIELDS = [
	dict(fieldname="jp_si_exemption_kind", label="社保免除の区分", fieldtype="Select",
		options=_opts((C.LEAVE_MATERNITY, C.LEAVE_CHILDCARE, C.LEAVE_POSTNATAL)), insert_after="is_lwp",
		description="産前産後休業・育児休業等の保険料免除の判定に使う（SIR-50）"),
]

LEAVE_APPLICATION_FIELDS = [
	dict(fieldname="jp_si_work_days", label="出生時育児休業中の就業日", fieldtype="Small Text", insert_after="description",
		allow_on_submit=1, depends_on="leave_type",
		description="YYYY-MM-DD を1行に1日。社保免除の14日の計算から除く（SIR-50）。休業中の出勤は勤怠では記録できないため、ここに入力する"),
]

EMPLOYEE_FIELDS: list = []  # 役員区分は B-1 の jp_si_exempt_reason に統合（employee_data.officer_type）

WORKPLACE_FIELDS = [
	dict(fieldname="jp_si_prefecture", label="社会保険の都道府県", fieldtype="Select", options=_opts(C.PREFECTURES),
		insert_after="address", description="協会けんぽの健康保険料率を決める都道府県（適用事業所の所在地。SIR-02・10）"),
]

CUSTOM_FIELDS = {
	"Salary Component": SALARY_COMPONENT_FIELDS,
	"Salary Slip": SALARY_SLIP_FIELDS,
	"Leave Type": LEAVE_TYPE_FIELDS,
	"Leave Application": LEAVE_APPLICATION_FIELDS,
	"Employee": EMPLOYEE_FIELDS,
	"JP Workplace": WORKPLACE_FIELDS,
}


def ensure_components():
	"""社保控除の種別ごとの給与項目を作る（同じ種別の項目が既にあれば作らない）。"""
	for name, abbr, component_type in C.DEFAULT_COMPONENTS:
		if frappe.db.exists("Salary Component", {"jp_si_component_kind": name}):
			continue
		if frappe.db.exists("Salary Component", name):
			frappe.db.set_value("Salary Component", name, "jp_si_component_kind", name)
			continue
		frappe.get_doc({
			"doctype": "Salary Component", "salary_component": name, "salary_component_abbr": abbr, "type": component_type,
			"jp_si_component_kind": name, "jp_si_pay_class": C.PAY_CLASS_EXCLUDED, "jp_ei_target": C.EI_EXCLUDED,
			"depends_on_payment_days": 0, "is_tax_applicable": 0, "remove_if_zero_valued": 1,
			"description": "社会保険・雇用保険の計算（japan_hr_compliance）で金額を入れる",
		}).insert(ignore_permissions=True)


def after_migrate():
	create_custom_fields(CUSTOM_FIELDS, update=True)
	ensure_components()
