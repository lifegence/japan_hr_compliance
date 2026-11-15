"""事業場マスタと制度別の保険の適用、通常の労働者の所定労働条件（従業員マスタ要件 B-1 v1.0 EMP-29・30）。

料率は持たない。労災保険の料率は B-5 の料率マスタを wc_business_type から引く。
"""

import frappe
from frappe import _
from frappe.utils import getdate

from japan_hr_compliance.jp_hr_core.contract import get_contract_at

SCHEME_SOCIAL = "健康保険・厚生年金"
SCHEME_EI = "雇用保険"
SCHEME_LABOUR = "労働保険"
SCOPE_SINGLE = "一元適用"
SCOPE_WC = "二元適用（労災保険）"
SCOPE_EI = "二元適用（雇用保険）"

# 労働保険番号を引く対象 → その対象に使える労働保険の行
LABOUR_TARGETS = {"労災保険": (SCOPE_SINGLE, SCOPE_WC), "雇用保険": (SCOPE_SINGLE, SCOPE_EI)}


def _in_period(row, date):
	return getdate(row.valid_from) <= date and (not row.valid_to or getdate(row.valid_to) >= date)


def get_insurance_unit(workplace, scheme, date, labour_target=None):
	"""事業場・制度・日付から、適用先の事業場と番号を返す（EMP-30）。

	Args:
		workplace: JP Workplace の name
		scheme: "健康保険・厚生年金" / "雇用保険" / "労働保険"
		date: 判定日
		labour_target: scheme が "労働保険" のとき "労災保険" または "雇用保険"。二元適用の事業場では
			対象ごとに別の労働保険番号が返る。省略時は一元適用の行だけを見る。

	Returns:
		frappe._dict | None: 子テーブルの行の項目に、``applied_workplace``（適用先の事業場。
		parent_workplace が空ならこの事業場）を加えたもの。該当がなければ None。
	"""
	date = getdate(date)
	scopes = None
	if scheme == SCHEME_LABOUR:
		scopes = LABOUR_TARGETS.get(labour_target, (SCOPE_SINGLE,))
	rows = frappe.get_all(
		"JP Workplace Insurance Unit",
		filters={"parent": workplace, "parenttype": "JP Workplace", "scheme": scheme},
		fields=["*"], order_by="valid_from desc",
	)
	for row in rows:
		if scopes and row.labour_scope not in scopes:
			continue
		if _in_period(row, date):
			row.applied_workplace = row.parent_workplace or workplace
			return row
	return None


def get_labour_insurance_no(workplace, target, date):
	"""労災保険・雇用保険のどちらかの労働保険番号（14桁）を返す。二元適用なら別の番号になる。"""
	row = get_insurance_unit(workplace, SCHEME_LABOUR, date, labour_target=target)
	return row.labour_ins_no if row else None


def get_employee_insurance_unit(employee, scheme, date, labour_target=None):
	"""従業員の所属事業場（契約履歴）と日付から、制度ごとの適用先・番号を返す。"""
	workplace = None
	contract = get_contract_at(employee, date)
	if contract:
		workplace = contract.workplace
	return get_insurance_unit(workplace, scheme, date, labour_target) if workplace else None


def get_standard_condition(company, workplace, job_category, date):
	"""4分の3基準の比較対象となる通常の労働者の所定労働条件を返す（EMP-29）。

	優先順位：事業場×業務区分 → 事業場の既定（業務区分が空） → 会社の既定（事業場・業務区分が空）。
	それぞれ適用開始日が date 以前で最も新しい行を使う。判定（4分の3以上か）は B-5 で行う。

	Returns:
		frappe._dict | None: ``weekly_hours``・``monthly_days``・``valid_from``・``name`` 等
	"""
	date = getdate(date)
	candidates = []
	if workplace and job_category:
		candidates.append((workplace, job_category))
	if workplace:
		candidates.append((workplace, None))
	candidates.append((None, None))
	for wp, job in candidates:
		rows = frappe.get_all(
			"JP Standard Worker Condition",
			filters={"company": company, "workplace": wp or ("is", "not set"), "job_category": job or ("is", "not set"),
				"valid_from": ("<=", date)},
			fields=["name", "company", "workplace", "job_category", "valid_from", "weekly_hours", "monthly_days"],
			order_by="valid_from desc", limit=1,
		)
		if rows:
			return rows[0]
	return None


def get_employee_standard_condition(employee, date):
	"""従業員のその日の契約（事業場・業務区分）から、比較対象の通常の労働者の条件を返す。"""
	contract = get_contract_at(employee, date)
	if not contract:
		return None
	return get_standard_condition(contract.company, contract.workplace, contract.job_category, date)


def validate_insurance_units(doc):
	"""JP Workplace の子テーブルの番号の形式と、同じ制度の適用期間の重複を検査する。"""
	from japan_hr_compliance.jp_hr_core.normalize import digits_only

	for row in doc.insurance_units:
		try:
			if row.labour_ins_no:
				row.labour_ins_no = digits_only(row.labour_ins_no, "労働保険番号", 14)
			if row.scheme == SCHEME_EI and row.office_no:
				row.office_no = digits_only(row.office_no, "雇用保険の適用事業所番号", 11)
		except ValueError as e:
			frappe.throw(_("行 {0}：{1}").format(row.idx, e))
		if row.scheme != SCHEME_LABOUR:
			row.labour_scope = None
		if row.valid_to and getdate(row.valid_to) < getdate(row.valid_from):
			frappe.throw(_("行 {0}：適用終了日が適用開始日より前です。").format(row.idx))
		if row.parent_workplace and row.parent_workplace == doc.name:
			row.parent_workplace = None

	def overlaps(a, b):
		a_end = getdate(a.valid_to) if a.valid_to else getdate("9999-12-31")
		b_end = getdate(b.valid_to) if b.valid_to else getdate("9999-12-31")
		return getdate(a.valid_from) <= b_end and getdate(b.valid_from) <= a_end

	def conflict(a, b):
		if a.scheme != b.scheme:
			return False
		if a.scheme != SCHEME_LABOUR:
			return True
		return a.labour_scope == b.labour_scope or SCOPE_SINGLE in (a.labour_scope, b.labour_scope)

	rows = list(doc.insurance_units)
	for i, a in enumerate(rows):
		for b in rows[i + 1:]:
			if conflict(a, b) and overlaps(a, b):
				frappe.throw(_("行 {0} と行 {1}：同じ制度の適用期間が重複しています。").format(a.idx, b.idx))


# --- 常時使用労働者数（EMP-33） ----------------------------------------------------------------------
DEFAULT_HEADCOUNT_EXCLUDED = ("日雇い",)


def headcount_excluded_classes():
	"""常時使用労働者数に含めない雇用区分（JP HR Settings。既定は日雇い）。"""
	value = frappe.db.get_single_value("JP HR Settings", "headcount_excluded_classes")
	if value is None or not str(value).strip():
		return set(DEFAULT_HEADCOUNT_EXCLUDED)
	return {x.strip() for x in str(value).replace("、", "\n").splitlines() if x.strip()}


def count_regular_workers(date, company=None, workplace=None):
	"""指定日時点の事業場ごとの常時使用労働者数（労基則53条2項の判断用）。

	雇用形態・契約期間・賃金支払の有無にかかわらず在籍者を数える（パート・アルバイト・休職者〔無給を含む〕・
	出向受入者を含む）。日雇い（と JP HR Settings で指定した雇用区分）は含めない。派遣労働者は従業員として
	登録しない前提（登録した場合は除外の雇用区分にする）。この数え方は本項目に限り、他の制度の人数要件
	（B-5 の特定適用事業所等）には使わない。

	Returns:
		dict[str, int]: {事業場（契約がない従業員は ""）: 人数}
	"""
	from japan_hr_compliance.jp_hr_core.address import active_employees

	date = getdate(date)
	excluded = headcount_excluded_classes()
	counts = {}
	for employee in active_employees(date, company):
		contract = get_contract_at(employee, date)
		if contract and contract.employment_class in excluded:
			continue
		wp = (contract.workplace if contract else None) or ""
		if workplace and wp != workplace:
			continue
		counts[wp] = counts.get(wp, 0) + 1
	return counts
