"""hrms との連携：年休の「器」（従業員ごとに長期の Leave Allocation 1本）と台帳行（B-3 3.1）。

方式（3.1 と検証結果）
- 器：年休の Leave Allocation を従業員ごとに1本作る。期間は最初の付与日から JP Leave Settings の
  「器の期間」（既定40年）。Leave Policy・繰越（carry_forward）は使わない。
- 付与：器を参照する Leave Ledger Entry（取引種別 Leave Allocation、is_expired=0）を付与日から器の末日まで
  の期間で追加する。最初の付与は器そのものの台帳行（new_leaves_allocated）を使う。
  hrms の Earned Leave の追加付与（hrms.hr.utils.create_additional_leave_ledger_entry）と同じ形の行で、
  hrms の残高計算（get_leave_balance_on）・Leave Balance レポートがそのまま付与日以後の残高に数える。
- 失効・消滅・取消：失効日の翌日（取消は付与日）から器の末日までの負の行（is_expired=0）を追加する。
  is_expired=1 の行は hrms の残高計算では「期間の末日より前」の行しか数えず、Leave Balance レポートの
  期間の境界で失効分が数えられない日が出るため使わない（検証で確認）。このため hrms の Leave Balance
  レポートでは失効分は「Leaves Expired」ではなく付与の減少として表示される。
- hrms の失効ジョブ（process_expired_allocation）は器の末日が未来のため器を失効させない。
- 台帳行は取り消さない（hrms は is_expired=0 の Leave Allocation の台帳行の取消を認めない）。訂正は負の行で行う。
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_years, flt, getdate

from japan_hr_compliance.jp_leave.utils import fl, get_settings

LEDGER = "Leave Ledger Entry"
ALLOCATION = "Leave Allocation"


def _container_filters(employee, leave_type):
	return {"employee": employee, "leave_type": leave_type, "docstatus": 1, "jp_leave_container": 1}


def get_container(employee, leave_type):
	name = frappe.db.get_value(ALLOCATION, _container_filters(employee, leave_type), "name")
	return frappe.get_doc(ALLOCATION, name) if name else None


def is_container(allocation):
	return bool(allocation.get("jp_leave_container"))


def add_grant(grant):
	"""年休付与の確定時に器へ付与日数を加える。作成・追加した台帳行の name を返す。"""
	settings = get_settings(grant.company)
	container = get_container(grant.employee, grant.leave_type)
	if not container:
		container = _create_container(grant, settings)
		return container, _first_ledger_row(container)
	if getdate(grant.grant_date) < getdate(container.from_date):
		container.db_set("from_date", grant.grant_date, update_modified=False)
	entry = _ledger_row(container, grant.days, grant.grant_date)
	_sync_total(container)
	return container, entry


def add_negative(grant, days, from_date):
	"""失効・消滅・取消の負の行を加える（days は正の数で渡す）。"""
	if not grant.leave_allocation or flt(days) <= 0:
		return None
	container = frappe.get_doc(ALLOCATION, grant.leave_allocation)
	entry = _ledger_row(container, -flt(days), from_date)
	_sync_total(container)
	return entry


def close_container(employee, leave_type, relieving_date):
	"""退職日で器を締める（LVR-34）。器の末日を退職日にする（台帳行の期間は変えない）。"""
	container = get_container(employee, leave_type)
	if container and getdate(container.to_date) > getdate(relieving_date):
		container.db_set("to_date", relieving_date, update_modified=False)
		container.add_comment("Info", _("退職日 {0} で年休の器を締めました（B-3 LVR-34）。").format(relieving_date))


def _create_container(grant, settings):
	from_date = getdate(grant.grant_date)
	to_date = add_days(add_years(from_date, int(settings.container_years or 40)), -1)
	doc = frappe.get_doc({
		"doctype": ALLOCATION, "employee": grant.employee, "leave_type": grant.leave_type, "company": grant.company,
		"from_date": from_date, "to_date": to_date, "new_leaves_allocated": flt(grant.days), "carry_forward": 0,
		"jp_leave_container": 1, "description": _("年次有給休暇の器（japan_hr_compliance が付与・失効の台帳行で増減する）"),
	})
	doc.flags.ignore_permissions = True
	doc.flags.jp_container = True
	doc.insert()
	doc.submit()
	return doc


def _first_ledger_row(container):
	return frappe.db.get_value(
		LEDGER, {"transaction_name": container.name, "transaction_type": ALLOCATION, "docstatus": 1}, "name",
		order_by="creation asc",
	)


def _ledger_row(container, leaves, from_date):
	doc = frappe.get_doc({
		"doctype": LEDGER, "employee": container.employee, "employee_name": container.employee_name,
		"leave_type": container.leave_type, "transaction_type": ALLOCATION, "transaction_name": container.name,
		"company": container.company, "leaves": flt(leaves), "from_date": getdate(from_date),
		"to_date": getdate(_ledger_end(container)), "is_carry_forward": 0, "is_expired": 0, "is_lwp": 0,
	})
	doc.flags.ignore_permissions = True
	doc.submit()
	return doc.name


def _ledger_end(container):
	"""台帳行の末日。器を退職日で締めた後も最初の行と同じ末日にそろえる（残高計算の期間を変えない）。"""
	first = frappe.db.get_value(
		LEDGER, {"transaction_name": container.name, "transaction_type": ALLOCATION, "docstatus": 1}, "to_date",
		order_by="creation asc",
	)
	return first or container.to_date


def _sync_total(container):
	"""器の new_leaves_allocated / total_leaves_allocated を台帳行の合計（付与－失効）にそろえる（表示用）。"""
	total = frappe.db.sql(
		"""select coalesce(sum(leaves), 0) from `tabLeave Ledger Entry`
		where transaction_name=%s and transaction_type='Leave Allocation' and docstatus=1 and is_expired=0""",
		container.name,
	)[0][0]
	container.db_set({"new_leaves_allocated": flt(total), "total_leaves_allocated": flt(total)}, update_modified=False)


def hrms_balance(employee, leave_type, date, for_consumption=False):
	"""hrms 標準の残高（get_leave_balance_on）。照合（LVR-32）とテストで使う。"""
	from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on

	if for_consumption:
		return fl(get_leave_balance_on(employee, leave_type, date, consider_all_leaves_in_the_allocation_period=True,
			for_consumption=True).get("leave_balance"))
	return fl(get_leave_balance_on(employee, leave_type, date))


def protect_container(doc, method=None):
	"""Leave Allocation の取消・変更から器を守る（台帳行がすべて消えるため）。"""
	if not is_container(doc) or doc.flags.jp_container:
		return
	if method == "before_cancel":
		if frappe.db.exists("JP Annual Leave Grant", {"leave_allocation": doc.name, "docstatus": 1}):
			frappe.throw(_("{0} は年次有給休暇の器です。確定済みの年休付与があるため取り消せません。年休付与の側で取消・調整してください。")
				.format(doc.name))
	elif method == "before_update_after_submit" and doc.has_value_changed("new_leaves_allocated"):
		frappe.throw(_("年次有給休暇の器の日数は直接変更できません。年休付与（JP Annual Leave Grant）で付与・調整してください。"))
