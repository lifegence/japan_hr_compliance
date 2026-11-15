"""年5日の取得義務の履行期間（LVR-50〜53）。年休付与の確定・取消で自動的に作り直す。"""

from frappe.model.document import Document
from frappe.utils import add_years, getdate

from japan_hr_compliance.jp_leave.utils import get_settings, throw_retention, today_


class JPFiveDayObligation(Document):
	def on_trash(self):
		if self.flags.jp_obsolete:
			return
		years = int(get_settings(self.company).retention_years or 5)
		until = add_years(getdate(self.period_end), max(years, 3))
		if getdate(today_()) <= until:
			throw_retention(self.doctype, until)
