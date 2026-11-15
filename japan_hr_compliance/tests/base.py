import frappe
from frappe.tests import IntegrationTestCase

from japan_hr_compliance.tests import fixtures as fx


class JPHRTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		fx.ensure_all()

	def tearDown(self):
		frappe.set_user("Administrator")
