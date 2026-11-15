"""Employee に追加する項目（従業員マスタ要件 B-1 v1.0 5章の P0・P1 分）。

権限レベル（EMP-85）
- 0：基本情報・住所・退職事由（人事担当が編集）
- 1：振込口座（給与担当が編集）
- 2：扶養・保険番号（人事担当が編集）

保険番号は名簿以外の帳票に出さないため print_hide にする（EMP-10）。
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

SEPARATION_TYPES = ["", "自己都合", "会社都合", "契約期間満了", "定年", "解雇", "死亡", "その他"]

# 被保険者でない場合の理由（EMP-12）。どの制度の対象外とみなすかは employee.SI_EXEMPT_SCHEMES
SI_EXEMPT_REASONS = ["", "適用除外（健康保険・厚生年金）", "適用除外（雇用保険）", "適用除外（すべて）", "役員（雇用保険の対象外）",
	"兼務役員（雇保は労働者部分で判定）", "70歳以上（厚生年金の資格喪失）", "75歳以上（後期高齢者医療）", "その他"]

# 離職証明書の離職理由の大区分（EMP-48・81）。名簿用の離職区分から自動で決めない
EI_SEPARATION_REASONS = ["", "1 事業所の倒産等によるもの", "2 定年によるもの", "3 労働契約期間満了等によるもの",
	"4 事業主からの働きかけによるもの（解雇・勧奨退職等）", "5 労働者の判断によるもの", "6 その他"]

# 退職時の住民税の徴収（EMP-82）
RESIDENT_TAX_ON_LEAVE = ["", "特別徴収の継続（転職先へ引継ぎ）", "一括徴収", "普通徴収への切替"]

LEVEL_BANK = 1
LEVEL_SENSITIVE = 2


def _ins(fieldname, label, insert_after, fieldtype="Data", **kw):
	return dict(fieldname=fieldname, label=label, fieldtype=fieldtype, insert_after=insert_after, permlevel=LEVEL_SENSITIVE,
		print_hide=1, report_hide=0, **kw)


EMPLOYEE_FIELDS = [
	# --- 氏名カナ（EMP-01） ---
	dict(fieldname="jp_last_name_kana", label="姓（カナ）", fieldtype="Data", insert_after="last_name", reqd=1,
		description="全角カタカナ。半角カナ・ひらがなは保存時に変換"),
	dict(fieldname="jp_first_name_kana", label="名（カナ）", fieldtype="Data", insert_after="jp_last_name_kana", reqd=1),
	# --- 旧姓（通称）（EMP-02） ---
	dict(fieldname="jp_former_name", label="旧姓（通称）", fieldtype="Data", insert_after="jp_first_name_kana",
		description="旧姓（姓のみ）。通称の表示は「旧姓＋名」"),
	dict(fieldname="jp_display_former_name", label="名簿以外の帳票で旧姓（通称）を表示する", fieldtype="Check",
		insert_after="jp_former_name", depends_on="jp_former_name",
		description="労働者名簿は常に戸籍上の氏名。給与明細等は通称で表示する"),
	# --- 振込口座（EMP-34。権限レベル1） ---
	dict(fieldname="jp_bank_section", label="振込口座（全銀フォーマット）", fieldtype="Section Break", insert_after="iban",
		permlevel=LEVEL_BANK),
	dict(fieldname="jp_bank_accounts", label="振込口座", fieldtype="Table", options="JP Employee Bank Account",
		insert_after="jp_bank_section", permlevel=LEVEL_BANK, print_hide=1,
		description="振込順位の小さい口座から定額を振り込み、定額のない口座（1件）に残額を振り込む"),
	# --- 日本の人事情報タブ ---
	dict(fieldname="jp_tab", label="日本の人事情報", fieldtype="Tab Break", insert_after="internal_work_history"),
	dict(fieldname="jp_address_section", label="住所（履歴）", fieldtype="Section Break", insert_after="jp_tab",
		description="転居の都度、適用開始日を付けて行を追加する（EMP-05・06）"),
	dict(fieldname="jp_addresses", label="住所", fieldtype="Table", options="JP Employee Address", insert_after="jp_address_section"),
	# --- 社会保険・雇用保険の番号（EMP-10・11。権限レベル2） ---
	dict(fieldname="jp_insurance_section", label="社会保険・雇用保険", fieldtype="Section Break", insert_after="jp_addresses",
		permlevel=LEVEL_SENSITIVE),
	_ins("jp_basic_pension_no", "基礎年金番号", "jp_insurance_section", description="10桁（4桁＋6桁）"),
	_ins("jp_emp_ins_no", "雇用保険被保険者番号", "jp_basic_pension_no", description="11桁（4桁－6桁－1桁）"),
	_ins("jp_insurance_cb1", None, "jp_emp_ins_no", fieldtype="Column Break"),
	_ins("jp_hi_insurer_no", "健康保険の保険者番号", "jp_insurance_cb1", description="8桁"),
	_ins("jp_hi_symbol", "健康保険の記号", "jp_hi_insurer_no", description="数字。先頭のゼロも入力"),
	_ins("jp_hi_number", "健康保険の番号", "jp_hi_symbol", description="数字。先頭のゼロも入力"),
	_ins("jp_hi_branch_no", "健康保険の枝番", "jp_hi_number", description="2桁"),
	_ins("jp_qualification_section", "資格取得日・喪失日", "jp_hi_branch_no", fieldtype="Section Break"),
	_ins("jp_hi_acquired", "健康保険 資格取得日", "jp_qualification_section", fieldtype="Date"),
	_ins("jp_hi_lost", "健康保険 資格喪失日", "jp_hi_acquired", fieldtype="Date"),
	_ins("jp_qualification_cb1", None, "jp_hi_lost", fieldtype="Column Break"),
	_ins("jp_pi_acquired", "厚生年金 資格取得日", "jp_qualification_cb1", fieldtype="Date"),
	_ins("jp_pi_lost", "厚生年金 資格喪失日", "jp_pi_acquired", fieldtype="Date"),
	_ins("jp_qualification_cb2", None, "jp_pi_lost", fieldtype="Column Break"),
	_ins("jp_ei_acquired", "雇用保険 資格取得日", "jp_qualification_cb2", fieldtype="Date"),
	_ins("jp_ei_lost", "雇用保険 資格喪失日", "jp_ei_acquired", fieldtype="Date"),
	_ins("jp_si_exempt_reason", "被保険者でない場合の理由", "jp_ei_lost", fieldtype="Select", options="\n".join(SI_EXEMPT_REASONS),
		description="区分に応じて保険料計算の対象外とする（判定規則は B-5）。兼務役員は雇用保険の判定から自動で除外しない"),
	# --- 扶養親族（EMP-40〜43。権限レベル2） ---
	_ins("jp_dependent_section", "扶養親族", "jp_si_exempt_reason", fieldtype="Section Break"),
	_ins("jp_dependent_change_date", "扶養の異動日", "jp_dependent_section", fieldtype="Date", no_copy=1,
		description="扶養親族の表を変更するときに異動日を入れる（空欄なら今日）。保存すると異動の記録と手続きタスクを作り、この欄は空に戻る"),
	_ins("jp_dependents", "扶養親族", "jp_dependent_change_date", fieldtype="Table", options="JP Employee Dependent"),
	_ins("jp_dependent_incomes", "年分ごとの所得見積と税の区分", "jp_dependents", fieldtype="Table", options="JP Dependent Income"),
	# --- 外国人の在留情報（EMP-04） ---
	dict(fieldname="jp_foreign_section", label="外国人の在留情報", fieldtype="Section Break", insert_after="jp_dependent_incomes",
		collapsible=1, description="在留期間の満了日の60日前（JP HR Settings で変更可）に人事担当へ通知する"),
	dict(fieldname="jp_romaji_name", label="ローマ字氏名", fieldtype="Data", insert_after="jp_foreign_section",
		description="在留カードの表記（大文字）"),
	dict(fieldname="jp_nationality", label="国籍・地域", fieldtype="Data", insert_after="jp_romaji_name"),
	dict(fieldname="jp_foreign_cb1", fieldtype="Column Break", insert_after="jp_nationality"),
	dict(fieldname="jp_residence_status", label="在留資格", fieldtype="Data", insert_after="jp_foreign_cb1"),
	dict(fieldname="jp_residence_expiry", label="在留期間の満了日", fieldtype="Date", insert_after="jp_residence_status"),
	_ins("jp_residence_card_no", "在留カード番号", "jp_residence_expiry", description="英字2桁＋数字8桁＋英字2桁"),
	# --- 名簿用の退職事由（EMP-44） ---
	dict(fieldname="jp_separation_section", label="労働者名簿の退職・死亡の記載", fieldtype="Section Break", insert_after="feedback"),
	dict(fieldname="jp_separation_type", label="離職区分（名簿用）", fieldtype="Select", options="\n".join(SEPARATION_TYPES),
		insert_after="jp_separation_section", mandatory_depends_on="relieving_date",
		description="離職票の離職理由とは別。ここから離職理由を自動で決めない"),
	dict(fieldname="jp_separation_reason", label="退職の事由", fieldtype="Small Text", insert_after="jp_separation_type"),
	dict(fieldname="jp_dismissal_reason", label="解雇の理由", fieldtype="Small Text", insert_after="jp_separation_reason",
		depends_on="eval:doc.jp_separation_type=='解雇'", mandatory_depends_on="eval:doc.jp_separation_type=='解雇'"),
	dict(fieldname="jp_separation_cb1", fieldtype="Column Break", insert_after="jp_dismissal_reason"),
	dict(fieldname="jp_death_date", label="死亡の年月日", fieldtype="Date", insert_after="jp_separation_cb1",
		depends_on="eval:doc.jp_separation_type=='死亡'", mandatory_depends_on="eval:doc.jp_separation_type=='死亡'"),
	dict(fieldname="jp_death_cause", label="死亡の原因", fieldtype="Small Text", insert_after="jp_death_date",
		depends_on="eval:doc.jp_separation_type=='死亡'", mandatory_depends_on="eval:doc.jp_separation_type=='死亡'"),
	# --- 退職時の手続き（EMP-48・81・82） ---
	dict(fieldname="jp_leave_procedure_section", label="退職時の手続き（雇用保険・住民税）", fieldtype="Section Break",
		insert_after="jp_death_cause", depends_on="relieving_date"),
	dict(fieldname="jp_separation_slip_requested", label="離職票の交付を希望する", fieldtype="Check",
		insert_after="jp_leave_procedure_section", description="希望がなくても離職日に59歳以上なら離職証明書のタスクを作成する"),
	dict(fieldname="jp_ei_separation_reason", label="離職証明書の離職理由", fieldtype="Select", options="\n".join(EI_SEPARATION_REASONS),
		insert_after="jp_separation_slip_requested", description="名簿用の離職区分とは別に選ぶ（自動では決めない）"),
	dict(fieldname="jp_leave_procedure_cb1", fieldtype="Column Break", insert_after="jp_ei_separation_reason"),
	dict(fieldname="jp_resident_tax_on_leave", label="退職時の住民税の徴収", fieldtype="Select",
		options="\n".join(RESIDENT_TAX_ON_LEAVE), insert_after="jp_leave_procedure_cb1",
		description="区分に応じた手続きタスクを作成し、住民税（B-7）に引き渡す"),
]

# 給与明細に表示する氏名（EMP-02。旧姓（通称）を選んだ従業員は通称）
SALARY_SLIP_FIELDS = [
	dict(fieldname="jp_display_name", label="表示する氏名", fieldtype="Data", insert_after="employee_name", read_only=1,
		description="旧姓（通称）の表示を選んだ従業員は通称"),
]


def _clean(field):
	return {k: v for k, v in field.items() if v is not None}


CUSTOM_FIELDS = {"Employee": [_clean(f) for f in EMPLOYEE_FIELDS], "Salary Slip": SALARY_SLIP_FIELDS}


def setup():
	create_custom_fields(CUSTOM_FIELDS, update=True)
