"""帳票の日本語フォント（japan_compliance 同梱の LifegenceJP）を使っていることの確認。"""
import os

import frappe

from japan_hr_compliance.tests.base import JPHRTestCase
from japan_hr_compliance.utils.print_fonts import FONT_STACK, jp_print_font_css, style_block


class TestPrintFonts(JPHRTestCase):
	def test_font_face_comes_from_japan_compliance(self):
		css = jp_print_font_css()
		self.assertIn('font-family: "LifegenceJP"', css)
		self.assertIn("/assets/japan_compliance/fonts/LifegenceJP-Regular.ttf", css)
		self.assertIn("/assets/japan_compliance/fonts/LifegenceJP-Bold.ttf", css)
		self.assertNotIn("/*", css)

	def test_font_files_exist(self):
		for name in ("LifegenceJP-Regular.ttf", "LifegenceJP-Bold.ttf"):
			path = frappe.get_app_path("japan_compliance", "public", "fonts", name)
			self.assertTrue(os.path.exists(path), path)

	def test_style_block_puts_bundled_font_first(self):
		block = style_block("td{padding:1px}")
		self.assertTrue(block.startswith("<style>") and block.endswith("</style>"))
		self.assertIn(f"body{{font-family:{FONT_STACK}}}", block)
		self.assertTrue(FONT_STACK.startswith('"LifegenceJP"'))

	def test_reports_and_notice_use_bundled_font(self):
		from japan_hr_compliance.jp_hr_core.report.jp_worker_roster import jp_worker_roster
		from japan_hr_compliance.jp_leave.report.jp_annual_leave_register import jp_annual_leave_register

		for mod in (jp_worker_roster, jp_annual_leave_register):
			src = open(mod.__file__, encoding="utf-8").read()
			self.assertIn("style_block(", src)
		tpl = open(frappe.get_app_path("japan_hr_compliance", "templates", "jp_labor_condition_notice.html"), encoding="utf-8").read()
		self.assertIn("jp_print_font_css()", tpl)
		self.assertIn("'LifegenceJP'", tpl)

	def test_pdf_embeds_bundled_font(self):
		"""PDF に LifegenceJP が埋め込まれる（OS のフォントに頼らない）。"""
		from frappe.utils.pdf import get_pdf

		html = "<html><head><meta charset='utf-8'>" + style_block() + "</head><body>労働者名簿 髙﨑 ①㈱</body></html>"
		pdf = get_pdf(html, {"page-size": "A4"})
		self.assertIn(b"LifegenceJP", pdf)
