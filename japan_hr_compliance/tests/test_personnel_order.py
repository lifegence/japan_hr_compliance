"""兼務・発令日のテスト（JP Concurrent Post／JP Personnel Order）。"""

import frappe
from frappe.utils import add_days, getdate, today

from japan_hr_compliance.jp_hr_core.doctype.jp_concurrent_post.jp_concurrent_post import active_posts, refresh_statuses
from japan_hr_compliance.jp_hr_core.doctype.jp_personnel_order.jp_personnel_order import apply_due_orders
from japan_hr_compliance.tests import fixtures as fx
from japan_hr_compliance.tests.base import JPHRTestCase


def ensure_department(name):
	full = frappe.db.get_value("Department", {"department_name": name, "company": fx.COMPANY})
	if full:
		return full
	return frappe.get_doc({"doctype": "Department", "department_name": name, "company": fx.COMPANY}).insert(ignore_permissions=True).name


def ensure_designation(name):
	if not frappe.db.exists("Designation", name):
		frappe.get_doc({"doctype": "Designation", "designation_name": name}).insert(ignore_permissions=True)
	return name


def order(employee, order_type, effective_date, submit=True, **kw):
	doc = frappe.get_doc({"doctype": "JP Personnel Order", "employee": employee, "order_type": order_type,
		"effective_date": effective_date, **kw}).insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


class TestPersonnelOrder(JPHRTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.sales = ensure_department("テスト営業部")
		cls.dev = ensure_department("テスト開発部")
		cls.pr = ensure_department("テスト広報室")
		cls.manager = ensure_designation("テスト課長")
		cls.staff = ensure_designation("テスト担当")
		frappe.db.commit()

	def tearDown(self):
		super().tearDown()
		frappe.db.rollback()

	def emp(self, **kw):
		return fx.make_employee(department=self.sales, designation=self.staff, **kw)

	def test_transfer_today_applies_on_submit(self):
		boss = self.emp()
		e = self.emp()
		o = order(e.name, "異動", today(), new_department=self.dev, new_designation=self.manager, new_reports_to=boss.name)
		self.assertEqual(o.status, "Applied")
		self.assertEqual((o.current_department, o.current_designation), (self.sales, self.staff))
		e.reload()
		self.assertEqual((e.department, e.designation, e.reports_to), (self.dev, self.manager, boss.name))
		hist = e.internal_work_history
		self.assertEqual(hist[-1].department, self.dev)
		self.assertEqual(getdate(hist[-1].from_date), getdate(today()))

	def test_future_order_is_scheduled_then_applied(self):
		e = self.emp()
		o = order(e.name, "昇格・降格", add_days(today(), 10), new_designation=self.manager)
		self.assertEqual(o.status, "Scheduled")
		self.assertEqual(frappe.db.get_value("Employee", e.name, "designation"), self.staff)
		apply_due_orders()  # not due yet
		self.assertEqual(frappe.db.get_value("JP Personnel Order", o.name, "status"), "Scheduled")
		frappe.db.set_value("JP Personnel Order", o.name, "effective_date", today())  # the day comes
		apply_due_orders()
		self.assertEqual(frappe.db.get_value("JP Personnel Order", o.name, "status"), "Applied")
		self.assertEqual(frappe.db.get_value("Employee", e.name, "designation"), self.manager)

	def test_cancel_only_before_applied(self):
		e = self.emp()
		future = order(e.name, "上長変更", add_days(today(), 5), new_reports_to=self.emp().name)
		future.cancel()
		self.assertEqual(frappe.db.get_value("JP Personnel Order", future.name, "status"), "Cancelled")
		applied = order(e.name, "上長変更", today(), new_reports_to=self.emp().name)
		with self.assertRaises(frappe.ValidationError):
			applied.cancel()

	def test_concurrent_post_and_its_end(self):
		boss = self.emp()
		e = self.emp()
		start = add_days(today(), -30)
		o = order(e.name, "兼務", start, new_department=self.pr, new_designation=self.manager, new_reports_to=boss.name)
		post = frappe.get_doc("JP Concurrent Post", o.created_post)
		self.assertEqual((post.department, post.reports_to, post.status), (self.pr, boss.name, "Active"))
		self.assertEqual([p.department for p in active_posts(e.name)], [self.pr])
		self.assertEqual(frappe.db.get_value("Employee", e.name, "department"), self.sales)  # main post unchanged
		order(e.name, "兼務解除", today(), concurrent_post=post.name)
		post.reload()
		self.assertEqual(getdate(post.to_date), getdate(add_days(today(), -1)))
		refresh_statuses()
		self.assertEqual(frappe.db.get_value("JP Concurrent Post", post.name, "status"), "Ended")
		self.assertEqual(active_posts(e.name), [])

	def test_rules(self):
		e = self.emp()
		with self.assertRaises(frappe.ValidationError):
			order(e.name, "異動", today(), submit=False)  # no department
		with self.assertRaises(frappe.ValidationError):
			order(e.name, "上長変更", today(), submit=False, new_reports_to=e.name)  # self
		with self.assertRaises(frappe.ValidationError):
			order(e.name, "兼務", today(), new_department=self.sales)  # same as the main post
		order(e.name, "兼務", add_days(today(), -10), new_department=self.pr)
		with self.assertRaises(frappe.ValidationError):
			order(e.name, "兼務", today(), new_department=self.pr)  # overlapping post in the same department
		other = self.emp()
		post = frappe.get_all("JP Concurrent Post", filters={"employee": e.name}, pluck="name")[0]
		with self.assertRaises(frappe.ValidationError):
			order(other.name, "兼務解除", today(), submit=False, concurrent_post=post)  # someone else's post
