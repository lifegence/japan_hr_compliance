"""事業場別の常時使用労働者数（JP）（従業員マスタ要件 B-1 v1.0 EMP-33）。数え方は workplace.count_regular_workers。"""

import frappe
from frappe import _
from frappe.utils import today

from japan_hr_compliance.jp_hr_core.workplace import count_regular_workers, headcount_excluded_classes


def execute(filters=None):
	filters = frappe._dict(filters or {})
	date = filters.date or today()
	counts = count_regular_workers(date, filters.company, filters.workplace)
	columns = [
		{"fieldname": "workplace", "label": _("事業場"), "fieldtype": "Link", "options": "JP Workplace", "width": 220},
		{"fieldname": "count", "label": _("常時使用労働者数"), "fieldtype": "Int", "width": 140},
	]
	data = [{"workplace": wp or _("（契約に事業場なし）"), "count": n} for wp, n in sorted(counts.items())]
	message = _("{0} 時点。パート・アルバイト・休職者・出向受入者を含み、次の雇用区分を含めない：{1}。名簿の業務欄は人数にかかわらず出力する。").format(
		date, "、".join(sorted(headcount_excluded_classes())))
	return columns, data, message
