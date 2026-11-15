import frappe
from frappe.tests import IntegrationTestCase

MODULES = ["JP HR Core", "JP Social Insurance", "JP Leave", "JP Payroll", "JP Withholding"]


class TestInstall(IntegrationTestCase):
    def test_required_apps_installed(self):
        installed = frappe.get_installed_apps()
        for app in ("hrms", "japan_compliance", "japan_hr_compliance"):
            self.assertIn(app, installed)

    def test_modules_registered(self):
        for module in MODULES:
            self.assertEqual(frappe.db.get_value("Module Def", module, "app_name"), "japan_hr_compliance")
