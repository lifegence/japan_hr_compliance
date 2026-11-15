"""社会保険・雇用保険（B-5）の選択肢と画面の値（料率・等級の値は持たない）。

DocType の Select の値と、計算エンジン（calc）の定数との対応をここに集める。
"""

from japan_hr_compliance.jp_social_insurance import calc

PREFECTURES = (
	"北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
	"茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
	"新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
	"静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
	"奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
	"徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
	"熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)

# --- 版の状態（SIR-06、5.3） ---
STATUS_DRAFT = "下書き"
STATUS_CONFIRMED = "確認済"
STATUS_SUPERSEDED = "廃止（訂正済）"
VERSION_STATUSES = (STATUS_DRAFT, STATUS_CONFIRMED, STATUS_SUPERSEDED)
STATUS_TO_CALC = {STATUS_CONFIRMED: calc.CONFIRMED, STATUS_DRAFT: calc.DRAFT, STATUS_SUPERSEDED: "superseded"}

# --- 保険者（SIR-02。第1次は協会けんぽのみ） ---
INSURER_KYOKAI = "協会けんぽ"

# --- 等級表の種別 ---
TABLE_HEALTH = "健康保険"
TABLE_PENSION = "厚生年金"
TABLE_KIND_TO_CALC = {TABLE_HEALTH: calc.HEALTH, TABLE_PENSION: calc.PENSION}

# --- 雇用保険の事業の種類（B-1 JP Workplace Insurance Unit.ei_business_class と同じ値） ---
EI_GENERAL = "一般の事業"
EI_BUSINESS_TYPES = (EI_GENERAL, "農林水産・清酒製造の事業", "建設の事業")

# --- 資格（SIR-11） ---
INS_HEALTH = "健康保険"
INS_PENSION = "厚生年金"
INS_EMPLOYMENT = "雇用保険"
INSURANCE_TYPES = (INS_HEALTH, INS_PENSION, INS_EMPLOYMENT)
# B-1 の Employee の資格取得日・喪失日（従業員マスタ要件 EMP-11）
B1_QUALIFICATION_FIELDS = {
	INS_HEALTH: ("jp_hi_acquired", "jp_hi_lost"),
	INS_PENSION: ("jp_pi_acquired", "jp_pi_lost"),
	INS_EMPLOYMENT: ("jp_ei_acquired", "jp_ei_lost"),
}
CATEGORY_GENERAL = "一般"
CATEGORY_SHORT_TIME = "短時間労働者"

# --- 標準報酬月額の決定区分（SIR-12・47） ---
DECISION_ACQUISITION = "資格取得時"
DECISION_REGULAR = "定時決定"
DECISION_OCCASIONAL = "随時改定"
DECISION_INSURER = "保険者算定"
DECISION_MATERNITY_END = "産休終了時改定"
DECISION_CHILDCARE_END = "育休終了時改定"
DECISION_TYPES = (
	DECISION_ACQUISITION, DECISION_REGULAR, DECISION_OCCASIONAL, DECISION_INSURER,
	DECISION_MATERNITY_END, DECISION_CHILDCARE_END,
)

# --- 決定の処理の種別（JP Remuneration Determination） ---
DET_REGULAR = "定時決定"
DET_OCCASIONAL = "随時改定"
DET_ANNUAL = "保険者算定（年間平均）"
DET_MATERNITY_END = "産休終了時改定"
DET_CHILDCARE_END = "育休終了時改定"
DETERMINATION_TYPES = (DET_REGULAR, DET_OCCASIONAL, DET_ANNUAL, DET_MATERNITY_END, DET_CHILDCARE_END)
DETERMINATION_TO_DECISION = {
	DET_REGULAR: DECISION_REGULAR,
	DET_OCCASIONAL: DECISION_OCCASIONAL,
	DET_ANNUAL: DECISION_INSURER,
	DET_MATERNITY_END: DECISION_MATERNITY_END,
	DET_CHILDCARE_END: DECISION_CHILDCARE_END,
}

# 支払基礎日数の規則の区分（SIR-42）
DAYS_GENERAL = "一般"
DAYS_PART_TIME = "一般（4分の3以上のパート等）"
DAYS_SHORT_TIME = "短時間労働者"
DAYS_CATEGORIES = (DAYS_GENERAL, DAYS_PART_TIME, DAYS_SHORT_TIME)
DAYS_TO_CALC = {
	DAYS_GENERAL: calc.GENERAL,
	DAYS_PART_TIME: calc.PART_TIME_THREE_QUARTERS,
	DAYS_SHORT_TIME: calc.SHORT_TIME,
}

DIRECTION_INCREASE = "増額"
DIRECTION_DECREASE = "減額"
DIRECTION_TO_CALC = {DIRECTION_INCREASE: calc.INCREASE, DIRECTION_DECREASE: calc.DECREASE}

# --- JP SI Settings の選択肢 ---
TIMING_NEXT = "翌月控除"
TIMING_CURRENT = "当月控除"
TIMING_TO_CALC = {TIMING_NEXT: calc.NEXT_MONTH, TIMING_CURRENT: calc.CURRENT_MONTH}
DISPLAY_COMBINED = "合算"
DISPLAY_SEPARATE = "別行"
DISPLAY_TO_CALC = {DISPLAY_COMBINED: calc.DISPLAY_COMBINED, DISPLAY_SEPARATE: calc.DISPLAY_SEPARATE}
BASIS_CLOSING = "賃金締切日"
BASIS_PAYMENT = "支給日"
BASIS_TO_CALC = {BASIS_CLOSING: calc.BASIS_CLOSING_DATE, BASIS_PAYMENT: calc.BASIS_PAYMENT_DATE}
PRESCRIBED_SLIP = "給与明細の勤務日数"
PRESCRIBED_CONTRACT = "契約の月所定労働日数"

DEFERRED_TRANSFER = "別途振込"
DEFERRED_LATER_PAY = "翌月以降の支給から控除"

# --- Salary Component の区分（7章） ---
PAY_CLASS_REMUNERATION = "報酬"
PAY_CLASS_BONUS = "賞与"
PAY_CLASS_EXCLUDED = "対象外"
FIXED = "固定"
NON_FIXED = "非固定"
EI_TARGET = "対象"
EI_EXCLUDED = "対象外"

# 社保控除の種別（Salary Component.jp_si_component_kind）
K_HEALTH_CARE = "健康保険料（介護含む）"
K_HEALTH = "健康保険料"
K_CARE = "介護保険料"
K_SUPPORT = "子ども・子育て支援金"
K_PENSION = "厚生年金保険料"
K_EMPLOYMENT = "雇用保険料"
K_ER_HEALTH = "事業主 健康保険料（介護含む）"
K_ER_SUPPORT = "事業主 子ども・子育て支援金"
K_ER_PENSION = "事業主 厚生年金保険料"
K_ER_CHILD = "事業主 子ども・子育て拠出金"
K_ER_EMPLOYMENT = "事業主 雇用保険料"
K_ER_WORKERS_COMP = "事業主 労災保険料"
DEDUCTION_KINDS = (K_HEALTH_CARE, K_HEALTH, K_CARE, K_SUPPORT, K_PENSION, K_EMPLOYMENT)
EMPLOYER_KINDS = (K_ER_HEALTH, K_ER_SUPPORT, K_ER_PENSION, K_ER_CHILD, K_ER_EMPLOYMENT, K_ER_WORKERS_COMP)
COMPONENT_KINDS = DEDUCTION_KINDS + EMPLOYER_KINDS

# 既定で作る Salary Component（名称, 略称, 種別）。金額は計算で入れるため式は持たない
DEFAULT_COMPONENTS = (
	(K_HEALTH_CARE, "JPHIC", "Deduction"),
	(K_HEALTH, "JPHI", "Deduction"),
	(K_CARE, "JPCI", "Deduction"),
	(K_SUPPORT, "JPCS", "Deduction"),
	(K_PENSION, "JPPI", "Deduction"),
	(K_EMPLOYMENT, "JPEI", "Deduction"),
	(K_ER_HEALTH, "JPERHI", "Employer Contribution"),
	(K_ER_SUPPORT, "JPERCS", "Employer Contribution"),
	(K_ER_PENSION, "JPERPI", "Employer Contribution"),
	(K_ER_CHILD, "JPERCC", "Employer Contribution"),
	(K_ER_EMPLOYMENT, "JPEREI", "Employer Contribution"),
	(K_ER_WORKERS_COMP, "JPERWC", "Employer Contribution"),
)

# calc.statement_lines の key → 控除の種別
LINE_KEY_TO_KIND = {"health_care": K_HEALTH_CARE, "health": K_HEALTH, "care": K_CARE, "support": K_SUPPORT,
	"pension": K_PENSION}

# --- 保険料の明細行（JP SI Premium Line）の区分 ---
LINE_MONTHLY = "月額"
LINE_BONUS = "賞与"
LINE_EMPLOYMENT = "雇用保険"
LINE_WORKERS_COMP = "労災保険"
P_HEALTH_CARE = "健康保険（介護含む）"
P_HEALTH = "健康保険"
P_SUPPORT = "子ども・子育て支援金"
P_PENSION = "厚生年金"
P_CHILD = "子ども・子育て拠出金"
P_EMPLOYMENT = "雇用保険"
P_WORKERS_COMP = "労災保険"
PREMIUM_KINDS = (P_HEALTH_CARE, P_HEALTH, P_SUPPORT, P_PENSION, P_CHILD, P_EMPLOYMENT, P_WORKERS_COMP)

# --- 休業の種類（Leave Type.jp_si_exemption_kind） ---
LEAVE_MATERNITY = "産前産後休業"
LEAVE_CHILDCARE = "育児休業等"
LEAVE_POSTNATAL = "出生時育児休業"
LEAVE_KIND_TO_CALC = {LEAVE_MATERNITY: calc.MATERNITY, LEAVE_CHILDCARE: calc.CHILDCARE,
	LEAVE_POSTNATAL: calc.POSTNATAL_CHILDCARE}

# --- 役員区分（B-4 PY-014）。B-1 の Employee.jp_si_exempt_reason から導く（employee_data.officer_type） ---
OFFICER = "役員"
CONCURRENT_OFFICER = "兼務役員"
EXEMPT_REASON_TO_OFFICER = {
	"役員（雇用保険の対象外）": OFFICER,
	"兼務役員（雇保は労働者部分で判定）": CONCURRENT_OFFICER,
}

# 賞与の扱い（calc の定数 → 画面の表示）
BONUS_TREATMENT_LABELS = {
	calc.TREATMENT_PREMIUM: "保険料あり",
	calc.TREATMENT_LOSS_MONTH: "喪失月（保険料なし・累計に算入）",
	calc.TREATMENT_NOT_COVERED: "対象外",
}

# 社保の適用区分（SIR-70）
COVERAGE_LABELS = {calc.COVERAGE_GENERAL: "一般", calc.COVERAGE_SHORT_TIME: "短時間労働者", calc.COVERAGE_NONE: "適用なし"}
