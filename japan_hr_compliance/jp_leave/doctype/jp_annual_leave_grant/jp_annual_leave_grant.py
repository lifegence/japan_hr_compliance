"""年休付与（付与1回ごとの正本。B-3 3.1、LVR-04〜08・20〜25・30）。

状態：予定・保留（下書き）→ 確定（Submit。hrms の器に台帳行を追加）→ 失効・消滅（日次処理）
出勤率8割未満の回は「付与なし」として確定し、記録を残す（LVR-14）。取消（Cancel）は充当がない場合だけ（LVR-25）。
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_years, getdate, now_datetime

from japan_hr_compliance.jp_leave import container, grant_service
from japan_hr_compliance.jp_leave.calc.ledger import expiry_date
from japan_hr_compliance.jp_leave.utils import (
	CALC,
	CONSUMPTION,
	GRANT,
	ST_ACTIVE,
	ST_CANCELLED,
	ST_EXPIRED,
	ST_EXTINGUISHED,
	ST_NO_GRANT,
	D,
	get_settings,
	throw_retention,
	today_,
)

HALF = D("0.5")
ADJUSTMENT = "調整"


class JPAnnualLeaveGrant(Document):
	def validate(self):
		settings = get_settings(self.company or frappe.db.get_value("Employee", self.employee, "company"))
		if not self.leave_type:
			self.leave_type = settings.annual_leave_type
		if self.is_manual:
			if not (self.reason or "").strip():
				frappe.throw(_("手動の付与・調整は理由を入力してください（LVR-24）。"))
			if not self.hire_date or self.has_value_changed("grant_date") or self.has_value_changed("round_no"):
				grant_service.fill_manual_defaults(self)
			if not self.days and self.grant_kind != ADJUSTMENT:
				self.days = self.table_days
		if self.days is None or self.days < 0:
			frappe.throw(_("付与日数は 0 以上にしてください。"))
		if (D(self.days) / HALF) != (D(self.days) / HALF).to_integral_value():
			frappe.throw(_("付与日数は 0.5 日単位で入力してください。"))
		self.expiry_date = expiry_date(getdate(self.grant_date), int(settings.validity_years or 2))
		if self.docstatus == 0:
			self.remaining_days = self.days if self.status != ST_NO_GRANT else 0
			self.used_days = 0
			self.lapsed_days = 0
		if self.grant_kind == ADJUSTMENT:
			# 調整（追加付与・訂正）は前倒し・分割の判定と年5日の回の累計に含めない
			self.flags.judge_errors = []
			self.judgement_ok = 1
			self.judgement = None
			self.manual_check_items = None
			self.five_day_target = 0
		else:
			self.flags.judge_errors = grant_service.judge(self)
		self.set_check_signature()

	def set_check_signature(self):
		if self.manual_checked and not self.checked_by:
			self.checked_by = frappe.session.user
			self.checked_on = now_datetime()
		elif not self.manual_checked:
			self.checked_by = None
			self.checked_on = None

	def before_submit(self):
		if self.status == ST_NO_GRANT:
			self.days = 0
			self.remaining_days = 0
			return
		if not self.days or self.days <= 0:
			frappe.throw(_("付与日数が 0 日です。出勤率8割未満で付与しない場合は「付与なし」として記録されます。"))
		if self.flags.judge_errors:
			frappe.throw("<br>".join(self.flags.judge_errors), title=_("前倒し・分割付与の要件を満たしていません（LVR-22）"))
		if grant_service.needs_manual_check(self) and not self.manual_checked:
			frappe.throw(_("システムで判定できない事項（就業規則の定め等）を確認し、確認のチェックを入れてください（LVR-22）。"),
				title=_("確認が必要です"))
		if self.grant_kind != ADJUSTMENT and frappe.db.exists(GRANT, {
			"employee": self.employee, "grant_date": self.grant_date, "docstatus": 1, "name": ("!=", self.name),
			"grant_kind": ("!=", ADJUSTMENT),
		}):
			frappe.throw(_("{0} の同じ付与日（{1}）の年休付与が確定済みです。").format(self.employee_name, self.grant_date))
		self.status = ST_ACTIVE
		self.pending_reasons = None
		self.remaining_days = self.days

	def on_submit(self):
		if self.attendance_calc:
			frappe.db.set_value(CALC, self.attendance_calc, "frozen", 1, update_modified=False)
		if self.status == ST_ACTIVE and self.days > 0:
			alloc, entry = container.add_grant(self)
			self.db_set({"leave_allocation": alloc.name, "ledger_entry": entry})
		if self.grant_kind == "分割" and self.round_no:
			# 同じ回の先の付与も「分割」として記録する（LVR-23）
			for name in frappe.get_all(GRANT, filters={"employee": self.employee, "round_no": self.round_no, "docstatus": 1,
					"name": ("!=", self.name), "grant_kind": ("in", ("前倒し", "法定どおり"))}, pluck="name"):
				frappe.db.set_value(GRANT, name, "grant_kind", "分割", update_modified=False)
		if self.is_manual:
			self.add_comment("Info", _("手動の付与（{0}）：{1} 日。理由：{2}").format(self.grant_kind, self.days, self.reason))
		from japan_hr_compliance.jp_leave import five_day_service

		five_day_service.rebuild(self.employee)

	def before_cancel(self):
		if self.status in (ST_EXPIRED, ST_EXTINGUISHED):
			frappe.throw(_("失効・消滅した年休付与は取り消せません。"))
		used = frappe.get_all(CONSUMPTION, filters={"grant": self.name, "cancelled": 0}, pluck="leave_application")
		if used:
			frappe.throw(
				_("この付与分から取得済みの休暇があるため取り消せません（{0}）。休暇申請の取消・調整を先に行ってください（LVR-25）。")
				.format(", ".join(sorted(set(used)))),
				title=_("充当があります"),
			)

	def on_cancel(self):
		entry = None
		if self.ledger_entry and self.days > 0:
			entry = container.add_negative(self, self.days, self.grant_date)
		self.db_set({"status": ST_CANCELLED, "remaining_days": 0, "cancel_ledger_entry": entry})
		if self.attendance_calc:
			frappe.db.set_value(CALC, self.attendance_calc, "frozen", 0, update_modified=False)
		from japan_hr_compliance.jp_leave import five_day_service

		five_day_service.rebuild(self.employee)

	def on_trash(self):
		if self.docstatus == 0:
			return
		until = retention_until(self)
		if getdate(today_()) <= until:
			throw_retention(self.doctype, until)


def retention_until(grant):
	"""管理簿の保存期間の末日。付与の期間（失効日。付与なしは付与日から1年）の満了後、設定の年数（LVR-61）。"""
	years = int(get_settings(grant.company).retention_years or 5)
	end = getdate(grant.expiry_date) if grant.expiry_date and grant.days else add_years(getdate(grant.grant_date), 1)
	return add_years(end, max(years, 3))
