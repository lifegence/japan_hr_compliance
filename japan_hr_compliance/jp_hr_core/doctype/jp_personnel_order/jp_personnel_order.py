"""発令（JP Personnel Order）。発令日付きで登録し、発令日に従業員マスタへ反映する。

- 異動：部署・役職・上長・支店を変える。昇格・降格：役職を変える。上長変更：上長を変える。
- 兼務：兼務（JP Concurrent Post）を作る。兼務解除：兼務の終了日を発令日の前日にする。
- 提出したとき、発令日が今日以前ならすぐ反映し、未来なら「予約」として毎日のスケジューラが発令日に反映する。
- 反映すると、従業員の社内職歴（internal_work_history）を発令日で区切る（異動・昇格・降格）。
- 反映前なら取消できる。反映後の取消はできない（逆の発令を出す）。
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate, now_datetime, today

NEEDS = {
	"異動": ("new_department",),
	"昇格・降格": ("new_designation",),
	"上長変更": ("new_reports_to",),
	"兼務": ("new_department",),
	"兼務解除": ("concurrent_post",),
}


class JPPersonnelOrder(Document):
	def validate(self):
		if self.docstatus == 0:
			self.status = "Draft"
			emp = frappe.db.get_value("Employee", self.employee, ["department", "designation", "reports_to", "branch", "status"], as_dict=True)
			if emp.status != "Active":
				frappe.throw(_("在籍中の従業員にだけ発令できます"))
			self.current_department, self.current_designation = emp.department, emp.designation
			self.current_reports_to, self.current_branch = emp.reports_to, emp.branch
		for field in NEEDS[self.order_type]:
			if not self.get(field):
				frappe.throw(_("{0} を入力してください").format(_(self.meta.get_label(field))))
		if self.new_reports_to and self.new_reports_to == self.employee:
			frappe.throw(_("本人を上長にはできません"))
		if self.order_type == "兼務解除":
			post = frappe.db.get_value("JP Concurrent Post", self.concurrent_post, ["employee", "from_date"], as_dict=True)
			if not post or post.employee != self.employee:
				frappe.throw(_("この従業員の兼務を選んでください"))
			if getdate(self.effective_date) <= getdate(post.from_date):
				frappe.throw(_("兼務解除の発令日は兼務開始日より後にしてください"))

	def on_submit(self):
		if getdate(self.effective_date) <= getdate(today()):
			self.apply()
		else:
			self.db_set("status", "Scheduled")

	def before_cancel(self):
		if self.status == "Applied":
			frappe.throw(_("反映済みの発令は取り消せません。元に戻す発令を出してください"))

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def apply(self):
		if self.status == "Applied":
			return
		date = getdate(self.effective_date)
		if self.order_type in ("異動", "昇格・降格", "上長変更"):
			emp = frappe.get_doc("Employee", self.employee)
			changes = {
				"department": self.new_department if self.order_type == "異動" else None,
				"designation": self.new_designation if self.order_type in ("異動", "昇格・降格") else None,
				"reports_to": self.new_reports_to if self.order_type in ("異動", "上長変更") else None,
				"branch": self.new_branch if self.order_type == "異動" else None,
			}
			changes = {k: v for k, v in changes.items() if v}
			if self.order_type != "上長変更":
				close_and_add_history(emp, date, changes)
			emp.update(changes)
			emp.flags.ignore_permissions = True
			emp.save()
		elif self.order_type == "兼務":
			post = frappe.get_doc(
				{
					"doctype": "JP Concurrent Post",
					"employee": self.employee,
					"department": self.new_department,
					"designation": self.new_designation,
					"reports_to": self.new_reports_to,
					"from_date": date,
					"personnel_order": self.name,
					"remarks": self.reason,
				}
			).insert(ignore_permissions=True)
			self.db_set("created_post", post.name)
		elif self.order_type == "兼務解除":
			post = frappe.get_doc("JP Concurrent Post", self.concurrent_post)
			post.to_date = add_days(date, -1)
			post.flags.ignore_permissions = True
			post.save()
		self.db_set({"status": "Applied", "applied_on": now_datetime()})


def close_and_add_history(emp, date, changes):
	"""社内職歴を発令日で区切る：今の行を前日で閉じ、発令日からの行を足す。"""
	rows = emp.get("internal_work_history") or []
	for row in rows:
		if not row.to_date:
			row.to_date = add_days(date, -1)
	emp.append(
		"internal_work_history",
		{
			"branch": changes.get("branch", emp.branch),
			"department": changes.get("department", emp.department),
			"designation": changes.get("designation", emp.designation),
			"from_date": date,
		},
	)


def apply_due_orders():
	"""毎日：発令日が来た予約中の発令を反映する（発令日の早い順）。"""
	for name in frappe.get_all(
		"JP Personnel Order",
		filters={"docstatus": 1, "status": "Scheduled", "effective_date": ("<=", today())},
		order_by="effective_date asc, creation asc",
		pluck="name",
	):
		doc = frappe.get_doc("JP Personnel Order", name)
		try:
			doc.apply()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(f"発令 {name} の反映に失敗しました", reference_doctype="JP Personnel Order", reference_name=name)
