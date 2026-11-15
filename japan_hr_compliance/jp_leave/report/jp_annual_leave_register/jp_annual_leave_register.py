"""年次有給休暇管理簿（LVR-60〜62、労基則24条の7）。

従業員ごと・基準日（付与）ごとに、基準日・付与日数（種別）・取得日（時季）と日数・区分・残日数・失効日数と、
年5日の履行期間・取得日数を出力する。履行期間が按分で基準日が2つある場合（ダブルトラック・分割付与）は
「基準日2」に後の基準日を出力する。PDF・Excel はレポート画面の出力のほか download（PDF／Excel）で出力する。
"""

import frappe

from japan_hr_compliance.utils.print_fonts import style_block
from frappe import _
from frappe.utils import add_years, getdate

from japan_hr_compliance.jp_leave.utils import CONSUMPTION, GRANT, OBLIGATION, ST_ACTIVE, ST_EXPIRED, ST_EXTINGUISHED, fl, today_

STATES = (ST_ACTIVE, ST_EXPIRED, ST_EXTINGUISHED)

COLUMNS = [
	("employee", _("従業員"), "Link", "Employee", 120),
	("employee_name", _("氏名"), "Data", None, 120),
	("department", _("部署"), "Link", "Department", 110),
	("base_date", _("基準日（付与日）"), "Date", None, 105),
	("base_date_2", _("基準日2"), "Date", None, 95),
	("legal_base_date", _("法定の基準日"), "Date", None, 100),
	("grant_kind", _("付与の種別"), "Data", None, 90),
	("days", _("付与日数"), "Float", None, 75),
	("takes", _("取得日（時季）・日数・区分"), "Small Text", None, 320),
	("taken_days", _("取得日数"), "Float", None, 75),
	("remaining_days", _("残日数"), "Float", None, 70),
	("lapsed_days", _("失効・消滅日数"), "Float", None, 90),
	("expiry_date", _("最終使用日"), "Date", None, 95),
	("status", _("状態"), "Data", None, 60),
	("five_day_period", _("年5日の履行期間"), "Data", None, 170),
	("five_day_required", _("年5日の必要日数"), "Float", None, 90),
	("five_day_taken", _("年5日の取得日数"), "Float", None, 90),
	("grant", _("年休付与"), "Link", GRANT, 130),
]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	cols = []
	for fieldname, label, fieldtype, options, width in COLUMNS:
		col = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width}
		if options:
			col["options"] = options
		cols.append(col)
	return cols


def default_period(filters):
	to_date = getdate(filters.get("to_date") or today_())
	from_date = getdate(filters.get("from_date") or add_years(to_date, -3))
	return from_date, to_date


def get_data(filters):
	from_date, to_date = default_period(filters)
	emp_filters = {}
	if filters.get("company"):
		emp_filters["company"] = filters.company
	if filters.get("department"):
		emp_filters["department"] = filters.department
	if filters.get("employee"):
		emp_filters["name"] = filters.employee
	employees = {e.name: e for e in frappe.get_all("Employee", filters=emp_filters, fields=["name", "employee_name", "department"])}
	if not employees:
		return []
	grants = frappe.get_all(
		GRANT,
		filters={"employee": ("in", list(employees)), "docstatus": 1, "status": ("in", STATES),
			"grant_date": ("<=", to_date), "expiry_date": (">=", from_date)},
		fields=["name", "employee", "grant_date", "legal_base_date", "grant_kind", "days", "remaining_days", "lapsed_days",
			"used_days", "expiry_date", "status"],
		order_by="employee asc, grant_date asc",
	)
	if not grants:
		return []
	takes = {}
	for c in frappe.get_all(CONSUMPTION, filters={"grant": ("in", [g.name for g in grants]), "cancelled": 0},
			fields=["grant", "leave_date", "days", "half_day_part", "take_kind"], order_by="leave_date asc"):
		takes.setdefault(c.grant, []).append(c)
	obligations = obligation_map(list(employees))
	data = []
	for g in grants:
		emp = employees[g.employee]
		rows = takes.get(g.name, [])
		ob = obligations.get(g.name)
		base2 = None
		if ob and ob.base_dates and len(ob.base_dates) > 1:
			others = [d for d in ob.base_dates if d != getdate(g.grant_date)]
			base2 = others[0] if others else None
		data.append({
			"employee": g.employee, "employee_name": emp.employee_name, "department": emp.department,
			"base_date": g.grant_date, "base_date_2": base2, "legal_base_date": g.legal_base_date,
			"grant_kind": g.grant_kind, "days": g.days, "takes": "、".join(format_take(c) for c in rows),
			"taken_days": fl(sum(fl(c.days) for c in rows)), "remaining_days": g.remaining_days,
			"lapsed_days": g.lapsed_days, "expiry_date": g.expiry_date, "status": g.status,
			"five_day_period": f"{ob.period_start}〜{ob.period_end}" if ob else None,
			"five_day_required": ob.required_days if ob else None, "five_day_taken": ob.taken_days if ob else None,
			"grant": g.name,
		})
	return data


def format_take(c):
	unit = _("半日・{0}").format(c.half_day_part) if fl(c.days) == 0.5 and c.half_day_part else (_("半日") if fl(c.days) == 0.5 else _("1日"))
	return f"{getdate(c.leave_date)}（{unit}・{c.take_kind or '本人の請求'}）"


def obligation_map(employees):
	"""年休付与 → 年5日の履行期間（基準日の一覧つき）。"""
	result = {}
	obs = frappe.get_all(OBLIGATION, filters={"employee": ("in", employees), "status": ("!=", "無効")},
		fields=["name", "period_start", "period_end", "required_days", "taken_days"])
	if not obs:
		return result
	by_name = {o.name: o for o in obs}
	for o in obs:
		o.base_dates = []
	for row in frappe.get_all("JP Five Day Obligation Grant", filters={"parent": ("in", list(by_name))},
			fields=["parent", "grant", "base_date"], order_by="idx asc"):
		ob = by_name[row.parent]
		if getdate(row.base_date) not in ob.base_dates:
			ob.base_dates.append(getdate(row.base_date))
		result[row.grant] = ob
	return result


# ------------------------------------------------------------------ PDF・Excel
def build_rows(filters):
	data = get_data(frappe._dict(filters))
	header = [c[1] for c in COLUMNS]
	rows = [[("" if d.get(c[0]) is None else d.get(c[0])) for c in COLUMNS] for d in data]
	return header, rows


def build_xlsx(filters):
	from frappe.utils.xlsxutils import make_xlsx

	header, rows = build_rows(filters)
	return make_xlsx([header] + [[str(v) if hasattr(v, "isoformat") else v for v in r] for r in rows],
		"年次有給休暇管理簿").getvalue()


def build_html(filters):
	header, rows = build_rows(filters)
	from_date, to_date = default_period(frappe._dict(filters))
	th = "".join(f"<th>{frappe.utils.escape_html(h)}</th>" for h in header)
	body = "".join("<tr>" + "".join(f"<td>{frappe.utils.escape_html(str(v))}</td>" for v in r) + "</tr>" for r in rows)
	return (
		"<html><head><meta charset='utf-8'>"
		+ style_block("body{font-size:8px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #000;padding:2px;vertical-align:top}")
		+ "</head>"
		f"<body><h3>年次有給休暇管理簿（{from_date}〜{to_date}）</h3><table><thead><tr>{th}</tr></thead>"
		f"<tbody>{body}</tbody></table></body></html>"
	)


def build_pdf(filters):
	from frappe.utils.pdf import get_pdf

	return get_pdf(build_html(filters), {"orientation": "Landscape", "page-size": "A4"})


@frappe.whitelist()
def download(filters=None, fmt="Excel"):
	"""管理簿を PDF または Excel で出力する（LVR-62）。"""
	frappe.has_permission(GRANT, "report", throw=True)
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	if fmt == "PDF":
		frappe.local.response.filename = "AnnualLeaveRegister.pdf"
		frappe.local.response.filecontent = build_pdf(filters)
	else:
		frappe.local.response.filename = "AnnualLeaveRegister.xlsx"
		frappe.local.response.filecontent = build_xlsx(filters)
	frappe.local.response.type = "download"
