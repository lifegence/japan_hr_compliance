"""労働条件通知書（従業員マスタ要件 B-1 v1.0 EMP-50〜52）。

明示事項の一覧（NOTICE_ITEMS）を、網羅チェックと雛形（templates/jp_labor_condition_notice.html）の両方で使う。

- 労基則5条1項の絶対的明示事項（2024年4月追加の就業場所・業務の変更の範囲、更新上限、無期転換の申込機会と
  転換後の労働条件を含む）
- パート有期法6条の追加事項（昇給・退職手当・賞与の有無、相談窓口）と、2026年10月追加の
  「待遇の相違の内容・理由等について説明を求めることができる旨」（雇入れ時・更新時の両方に入れる）
- 相対的明示事項（退職手当・賞与・休職等）は定めがある場合だけ記載し、未入力でも発行を止めない
"""

import frappe
from frappe.utils import cint, flt, getdate

# 2026年10月施行の追加明示事項（パート有期法6条）の定型文。項目が空欄なら差し込む
EXPLANATION_RIGHT_TEXT = (
	"あなたは、通常の労働者との間の待遇の相違の内容及び理由等について、会社に説明を求めることができます。"
	"説明を求めたことを理由として、不利益な取扱いを受けることはありません。"
)

PART_TIME_CLASSES = ("パートタイマー", "アルバイト")

ALWAYS, FIXED, PART, CONVERSION, OPTIONAL = "always", "fixed_term", "part_or_fixed", "conversion", "optional"

# (項目名, 表示名, 根拠, 必要になる条件)
NOTICE_ITEMS = (
	("contract_period_text", "労働契約の期間", "労基則5条1項1号", ALWAYS),
	("renewal_text", "契約の更新の有無・更新の判断の基準", "労基則5条1項1号の2", FIXED),
	("renewal_limit_text", "更新上限（通算契約期間または更新回数の上限）", "労基則5条1項1号の2（2024年4月追加）", FIXED),
	("place_text", "就業の場所", "労基則5条1項1号の3", ALWAYS),
	("change_scope_place", "就業の場所の変更の範囲", "労基則5条1項1号の3（2024年4月追加）", ALWAYS),
	("job_text", "従事すべき業務", "労基則5条1項1号の3", ALWAYS),
	("change_scope_job", "業務の変更の範囲", "労基則5条1項1号の3（2024年4月追加）", ALWAYS),
	("hours_text", "所定労働時間・所定労働日数", "労基則5条1項2号", ALWAYS),
	("work_time_text", "始業・終業の時刻", "労基則5条1項2号", ALWAYS),
	("overtime_text", "所定労働時間を超える労働の有無", "労基則5条1項2号", ALWAYS),
	("break_text", "休憩時間", "労基則5条1項2号", ALWAYS),
	("holiday_text", "休日", "労基則5条1項2号", ALWAYS),
	("leave_text", "休暇", "労基則5条1項2号", ALWAYS),
	("shift_text", "交替制の就業時転換", "労基則5条1項2号", ALWAYS),
	("wage_text", "賃金の決定・計算・支払の方法", "労基則5条1項3号", ALWAYS),
	("wage_payment_text", "賃金の締切り・支払の時期", "労基則5条1項3号", ALWAYS),
	("raise_text", "昇給に関する事項", "労基則5条1項3号", ALWAYS),
	("retirement_text", "退職に関する事項（解雇の事由を含む）", "労基則5条1項4号", ALWAYS),
	("conversion_opportunity_text", "無期転換の申込機会", "労基則5条5項（2024年4月追加）", CONVERSION),
	("conversion_conditions_text", "無期転換後の労働条件", "労基則5条6項（2024年4月追加）", CONVERSION),
	("pt_raise", "昇給の有無", "パート有期法6条1項・同法施行規則2条", PART),
	("pt_retirement_allowance", "退職手当の有無", "パート有期法6条1項・同法施行規則2条", PART),
	("pt_bonus", "賞与の有無", "パート有期法6条1項・同法施行規則2条", PART),
	("consultation_desk", "相談窓口", "パート有期法6条1項・同法施行規則2条", PART),
	("explanation_right_text", "待遇の相違の内容・理由等の説明を求めることができる旨", "パート有期法6条（2026年10月追加）", PART),
	("other_terms_text", "その他（退職手当・賞与・休職等、定めがある場合）", "労基則5条1項4号の2〜11号", OPTIONAL),
)


def _hm(hours):
	hours = flt(hours)
	h = int(hours)
	m = round((hours - h) * 60)
	return f"{h}時間{m}分" if m else f"{h}時間"


def is_part_or_fixed(contract):
	"""パートタイム・有期雇用労働者か（有期契約、パートタイマー・アルバイトの区分、またはパート用の就業規則）。"""
	return bool(cint(contract.fixed_term) or contract.employment_class in PART_TIME_CLASSES
		or contract.work_rule_class == "パートタイマー用")


def contract_values(contract):
	"""雇用契約から差し込む明示事項の文言。"""
	values = {}
	if cint(contract.fixed_term):
		values["contract_period_text"] = f"期間の定めあり（{getdate(contract.contract_start)}〜{getdate(contract.contract_end)}）"
		criteria = (contract.renewal_criteria or "").strip()
		values["renewal_text"] = "、".join(x for x in (contract.renewal, f"判断の基準：{criteria}" if criteria else "") if x) if contract.renewal else ""
		if contract.renewal == "更新しない":
			values["renewal_text"] = "更新しない"
		limits = []
		if cint(contract.renewal_limit_count):
			limits.append(f"更新回数 {cint(contract.renewal_limit_count)} 回まで")
		if flt(contract.renewal_limit_years):
			limits.append(f"通算契約期間 {flt(contract.renewal_limit_years):g} 年まで")
		values["renewal_limit_text"] = "、".join(limits)
	else:
		values["contract_period_text"] = f"期間の定めなし（{getdate(contract.contract_start or contract.valid_from)}から）"
		values["renewal_text"] = ""
		values["renewal_limit_text"] = ""
	workplace = frappe.db.get_value("JP Workplace", contract.workplace, ["workplace_name", "address"], as_dict=True) if contract.workplace else None
	values["place_text"] = "　".join(x for x in (workplace.workplace_name, workplace.address) if x) if workplace else ""
	job = frappe.db.get_value("JP Job Category", contract.job_category, "job_name") if contract.job_category else None
	values["job_text"] = job or ""
	values["change_scope_place"] = contract.change_scope_place or ""
	values["change_scope_job"] = contract.change_scope_job or ""
	parts = []
	if flt(contract.daily_hours):
		parts.append(f"1日 {_hm(contract.daily_hours)}")
	if flt(contract.weekly_hours):
		parts.append(f"週 {_hm(contract.weekly_hours)}")
	if flt(contract.weekly_days):
		parts.append(f"週 {flt(contract.weekly_days):g} 日")
	elif cint(contract.annual_days):
		parts.append(f"年間 {cint(contract.annual_days)} 日（{contract.annual_days_basis or ''}）")
	values["hours_text"] = "、".join(parts)
	return values


def required_items(doc):
	"""この通知書で必須の明示事項（項目名, 表示名, 根拠）。"""
	fixed = cint(frappe.db.get_value("JP Employment Contract", doc.contract, "fixed_term")) if doc.contract else 0
	result = []
	for fieldname, label, basis, cond in NOTICE_ITEMS:
		if cond == ALWAYS or (cond == FIXED and fixed) or (cond == PART and doc.part_or_fixed) or (
				cond == CONVERSION and cint(doc.conversion_right)):
			result.append((fieldname, label, basis))
	return result


def missing_items(doc):
	return [label for fieldname, label, _basis in required_items(doc) if not (str(doc.get(fieldname) or "")).strip()]


def items_for_print(doc):
	"""雛形に出す明示事項（必須の項目と、入力のある任意の項目）。"""
	rows = []
	required = {f for f, _l, _b in required_items(doc)}
	for fieldname, label, basis, _cond in NOTICE_ITEMS:
		value = (str(doc.get(fieldname) or "")).strip()
		if fieldname in required or value:
			rows.append(frappe._dict(label=label, value=value, basis=basis))
	return rows


def jp_labor_notice_items(doc):
	"""雛形（Jinja）から呼ぶ。hooks の jinja.methods で関数名のまま公開される。"""
	return items_for_print(doc)
