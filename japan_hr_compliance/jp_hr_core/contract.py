"""雇用契約履歴の共通関数（従業員マスタ要件 B-1 v1.0 EMP-20〜26・32）。

年次有給休暇の比例付与（B-3）と社会保険・雇用保険の適用判定（B-5）は、任意の日付時点の
契約条件を必ず :func:`get_contract_at` から取得する。JP Employment Contract を直接検索しないこと
（確定・取消・退職日の扱いをここに集約している）。
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, today

CONTRACT_DOCTYPE = "JP Employment Contract"


def _d(value):
	"""getdate(None) は今日を返すため、空は None のまま扱う。"""
	return getdate(value) if value else None


def get_contract_at(employee, date):
	"""指定した日付時点で有効な雇用契約（JP Employment Contract）を1件返す。

	B-3（年休の比例付与）・B-5（社会保険・雇用保険の適用判定）が使う公開 API。

	Args:
		employee (str): Employee の name（例："HR-EMP-00001"）。
		date (str | datetime.date): 判定する日付。文字列は "YYYY-MM-DD"。

	Returns:
		frappe._dict | None: 有効な契約の全項目（DocType の項目名がキー）。主な項目は次のとおり。

		- ``name`` / ``valid_from`` / ``valid_to``：契約の識別子と適用期間（valid_to は次の契約の前日か退職日。最新の在籍中の契約は None）
		- ``employment_class``（雇用区分）、``work_rule_class``（就業規則の区分。B-3 の付与テーブルの選択に使う）
		- ``weekly_hours``（週所定労働時間。時間単位の小数。例：37.5）、``weekly_days``（週所定労働日数）
		- ``annual_days``（年間所定労働日数）と ``annual_days_basis``（"確定" / "目安"）。週で定めない者は weekly_days が空で annual_days が必ず入る
		- ``daily_hours``、``monthly_days``（月所定労働日数）
		- ``fixed_term``（有期なら 1）、``contract_start`` / ``contract_end``、``expected_period``（雇用見込み期間）、``student_type``（学生区分）
		- ``wage_type``、``working_time_system``、``is_manager``
		- ``workplace``（JP Workplace）、``job_category``（JP Job Category）、``department``、``designation``

		次の場合は None を返す。

		- 入社日（date_of_joining）より前の日付
		- 退職日（relieving_date）より後の日付（退職日当日は在籍として契約を返す）
		- その日付を含む確定済み（docstatus=1）の契約がない

	Notes:
		- 対象は確定済みの契約だけ。下書き・取消済みは見ない。
		- 契約は適用開始日の順に重複・空白なく並ぶことを保存時に保証している（EMP-20）。
		  そのため「適用開始日が date 以前で最も新しい確定済みの契約」をその日の契約とする。
		- 有期契約の契約終了日を過ぎても次の契約が登録されていなければ、直前の契約を返す
		  （更新手続きの遅れで判定が空になるのを避ける。終了日の到来は EMP-23 の通知で知らせる）。
		- 権限の確認はしない（サーバ側の計算から呼ぶ前提）。画面から使う場合は呼び出し側で確認する。

	Example:
		>>> # 2027-04-01 に週5日・40h から週3日・24h に変更した従業員
		>>> get_contract_at("HR-EMP-00001", "2027-03-31").weekly_days
		5.0
		>>> get_contract_at("HR-EMP-00001", "2027-04-01").weekly_days
		3.0
	"""
	if not employee or not date:
		return None
	date = getdate(date)
	emp = frappe.db.get_value("Employee", employee, ["date_of_joining", "relieving_date"], as_dict=True)
	if not emp:
		return None
	if emp.date_of_joining and date < getdate(emp.date_of_joining):
		return None
	if emp.relieving_date and date > getdate(emp.relieving_date):
		return None
	rows = frappe.get_all(
		CONTRACT_DOCTYPE,
		filters={"employee": employee, "docstatus": 1, "valid_from": ("<=", date)},
		fields=["*"],
		order_by="valid_from desc",
		limit=1,
	)
	return rows[0] if rows else None


def get_contract_history(employee):
	"""確定済みの契約を適用開始日の昇順で返す（名簿の履歴欄・検証用）。"""
	return frappe.get_all(
		CONTRACT_DOCTYPE, filters={"employee": employee, "docstatus": 1}, fields=["*"], order_by="valid_from asc"
	)


def get_workplace_at(employee, date):
	"""指定日の所属事業場（JP Workplace の name）。契約がなければ None（EMP-32）。"""
	contract = get_contract_at(employee, date)
	return contract.workplace if contract else None


def refresh_valid_to(employee):
	"""確定済みの契約の適用終了日を、次の契約の前日（最後の契約は退職日）に揃える。"""
	relieving = _d(frappe.db.get_value("Employee", employee, "relieving_date"))
	rows = frappe.get_all(
		CONTRACT_DOCTYPE, filters={"employee": employee, "docstatus": 1}, fields=["name", "valid_from", "valid_to"],
		order_by="valid_from asc",
	)
	for i, row in enumerate(rows):
		new = getdate(add_days(rows[i + 1].valid_from, -1)) if i + 1 < len(rows) else relieving
		if _d(row.valid_to) != new:
			frappe.db.set_value(CONTRACT_DOCTYPE, row.name, "valid_to", new, update_modified=False)


def validate_chain(doc):
	"""契約の重複・空白を検査する（EMP-20）。doc は確定前の契約。確定済みの他の契約と突き合わせる。

	- 適用開始日が他の確定済み契約と同じ → 重複
	- 最初の契約の適用開始日が入社日と異なる → 入社日からの空白
	- 有期契約の終了日の翌日より後に次の契約が始まる → 契約の間の空白
	- 入社日より前・退職日より後に始まる契約 → 在籍期間外
	"""
	emp = frappe.db.get_value("Employee", doc.employee, ["date_of_joining", "relieving_date"], as_dict=True)
	valid_from = getdate(doc.valid_from)
	joining = _d(emp.date_of_joining)
	relieving = _d(emp.relieving_date)
	if joining and valid_from < joining:
		frappe.throw(_("適用開始日（{0}）が入社日（{1}）より前です。").format(valid_from, joining))
	if relieving and valid_from > relieving:
		frappe.throw(_("適用開始日（{0}）が退職日（{1}）より後です。").format(valid_from, relieving))

	others = [
		r for r in get_contract_history(doc.employee) if r.name != doc.name
	]
	for r in others:
		if getdate(r.valid_from) == valid_from:
			frappe.throw(_("同じ適用開始日（{0}）の確定済みの契約 {1} があり、期間が重複します。訂正する場合は {1} を取り消して修正してください。")
				.format(valid_from, r.name), title=_("契約の重複"))

	chain = sorted(others + [frappe._dict(doc.as_dict())], key=lambda r: getdate(r.valid_from))
	first = getdate(chain[0].valid_from)
	if joining and first != joining:
		frappe.throw(_("最初の契約の適用開始日（{0}）が入社日（{1}）と異なり、入社日からの期間に契約のない空白があります。")
			.format(first, joining), title=_("契約の空白"))
	for prev, nxt in zip(chain, chain[1:]):
		if prev.fixed_term and prev.contract_end and getdate(prev.contract_end) < getdate(add_days(nxt.valid_from, -1)):
			frappe.throw(_("有期契約の終了日（{0}）の翌日から次の契約の適用開始日（{1}）の前日までの間に契約のない空白があります。")
				.format(getdate(prev.contract_end), getdate(nxt.valid_from)), title=_("契約の空白"))


def sync_employee(employee, on_date=None):
	"""指定日（既定は今日）の契約の所属・雇用形態を Employee の標準項目に反映する（EMP-22・32）。

	branch（事業場の支店）・department・designation・employment_type を更新し、所属が変わったときは
	internal_work_history（社内の異動歴）に行を加える。
	"""
	on_date = getdate(on_date or today())
	contract = get_contract_at(employee, on_date)
	if not contract:
		return
	emp = frappe.get_doc("Employee", employee)
	branch = frappe.db.get_value("JP Workplace", contract.workplace, "branch") if contract.workplace else None
	values = {"employment_type": contract.employment_type, "department": contract.department,
		"designation": contract.designation, "branch": branch}
	values = {k: v for k, v in values.items() if v}
	changed = {k: v for k, v in values.items() if emp.get(k) != v}
	if not changed:
		return
	moved = any(k in changed for k in ("branch", "department", "designation"))
	emp.update(changed)
	if moved:
		start = getdate(contract.valid_from)
		for row in emp.internal_work_history:
			if not row.to_date and (not row.from_date or getdate(row.from_date) < start):
				row.to_date = add_days(start, -1)
		emp.append("internal_work_history", {"branch": emp.branch, "department": emp.department,
			"designation": emp.designation, "from_date": start})
	emp.flags.jp_contract_sync = True
	emp.save(ignore_permissions=True)


def sync_contracts_effective_today():
	"""日次：今日から適用される契約を Employee に反映する。"""
	for employee in frappe.get_all(CONTRACT_DOCTYPE, filters={"docstatus": 1, "valid_from": today()}, pluck="employee"):
		sync_employee(employee)


def notify_fixed_term_ending():
	"""日次：有期契約の終了日の N 日前（JP HR Settings、既定30日）に人事担当へ通知する（EMP-23）。

	終了日より後に始まる確定済みの契約（更新後の契約）があれば通知しない。
	"""
	days = frappe.db.get_single_value("JP HR Settings", "contract_end_notice_days") or 30
	target = add_days(today(), days)
	contracts = frappe.get_all(
		CONTRACT_DOCTYPE, filters={"docstatus": 1, "fixed_term": 1, "contract_end": target},
		fields=["name", "employee", "employee_name", "contract_end"],
	)
	if not contracts:
		return []
	recipients = hr_users()
	sent = []
	for c in contracts:
		if frappe.db.exists(CONTRACT_DOCTYPE, {"employee": c.employee, "docstatus": 1, "valid_from": (">", c.contract_end)}):
			continue
		for user in recipients:
			frappe.get_doc({
				"doctype": "Notification Log", "for_user": user, "type": "Alert", "document_type": CONTRACT_DOCTYPE,
				"document_name": c.name, "subject": _("有期契約の終了日が近づいています：{0}（{1} 終了）").format(c.employee_name, c.contract_end),
			}).insert(ignore_permissions=True)
		sent.append(c.name)
	return sent


def hr_users():
	from japan_hr_compliance.setup.roles import HR_USER

	users = frappe.get_all("Has Role", filters={"role": HR_USER, "parenttype": "User"}, pluck="parent")
	return [u for u in set(users) if frappe.db.get_value("User", u, "enabled") and u not in ("Administrator", "Guest")]


# --- 遡及変更の影響（EMP-27） ------------------------------------------------------------------------
def retroactive_period(doc):
	"""契約 doc が適用される期間のうち、今日以前の部分 (開始, 終了)。今日より後に始まる契約は None。"""
	start = getdate(doc.valid_from)
	end = getdate(today())
	if start > end:
		return None
	later = frappe.get_all(CONTRACT_DOCTYPE, filters={"employee": doc.employee, "docstatus": 1, "valid_from": (">", start),
		"name": ("not in", [doc.name, doc.amended_from or ""])}, fields=["valid_from"], order_by="valid_from asc", limit=1)
	if later:
		end = min(end, getdate(add_days(later[0].valid_from, -1)))
	relieving = _d(frappe.db.get_value("Employee", doc.employee, "relieving_date"))
	if relieving:
		end = min(end, relieving)
	return (start, end) if start <= end else None


def get_retroactive_impact(doc):
	"""遡及変更で影響を受ける付与・控除の一覧（文字列）を返す。

	- 年休の付与：hrms の Leave Allocation（確定済み）のうち、付与日（from_date）が期間内のもの
	- 保険料の控除：hrms の Salary Slip（確定済み）のうち、対象期間が期間と重なるもの
	- 他のモジュール（B-3・B-5 の独自の記録）は hooks の ``jp_contract_retro_impact`` に
	  ``fn(employee, start, end) -> list[str]`` を登録して影響を加えられる
	"""
	period = retroactive_period(doc)
	if not period:
		return []
	start, end = period
	impacts = []
	for row in frappe.get_all("Leave Allocation", filters={"employee": doc.employee, "docstatus": 1,
			"from_date": ("between", [start, end])}, fields=["name", "leave_type", "from_date", "new_leaves_allocated"], order_by="from_date asc"):
		impacts.append(_("年休の付与 {0}（{1}、{2} 付与、{3} 日）").format(row.name, row.leave_type, row.from_date, flt(row.new_leaves_allocated)))
	for row in frappe.get_all("Salary Slip", filters={"employee": doc.employee, "docstatus": 1, "start_date": ("<=", end),
			"end_date": (">=", start)}, fields=["name", "start_date", "end_date"], order_by="start_date asc"):
		impacts.append(_("給与明細 {0}（{1}〜{2}。保険料の控除）").format(row.name, row.start_date, row.end_date))
	for method in frappe.get_hooks("jp_contract_retro_impact"):
		impacts.extend(frappe.get_attr(method)(doc.employee, start, end) or [])
	return impacts
