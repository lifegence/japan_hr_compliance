"""住所履歴（従業員マスタ要件 B-1 v1.0 EMP-05・06）。住民税の1月1日時点の住所地などに使う。"""

from collections import Counter

import frappe
from frappe.utils import getdate


def format_address(row):
	if not row:
		return ""
	head = f"〒{row.postal_code} " if row.postal_code else ""
	return head + "".join(x for x in (row.prefecture, row.municipality, row.address_line) if x) + (
		f" {row.building}" if row.building else "")


def get_address_at(employee, date):
	"""指定日時点の住所（JP Employee Address の行）を返す。

	適用開始日が date 以前で最も新しい行。該当がなければ None。
	返り値には ``full_address``（郵便番号から建物名までをつないだ文字列）を加える。

	Example:
		>>> get_address_at("HR-EMP-00001", "2027-01-01").lg_code  # 1月中に転居しても転居前のコード
		'131164'
	"""
	rows = frappe.get_all(
		"JP Employee Address",
		filters={"parent": employee, "parenttype": "Employee", "parentfield": "jp_addresses", "valid_from": ("<=", getdate(date))},
		fields=["valid_from", "postal_code", "prefecture", "municipality", "lg_code", "address_line", "building"],
		order_by="valid_from desc", limit=1,
	)
	if not rows:
		return None
	row = rows[0]
	row.full_address = format_address(row)
	return row


def active_employees(date, company=None):
	"""指定日に在籍している従業員（入社日以前・退職日以後でない）。"""
	date = getdate(date)
	filters = {"date_of_joining": ("<=", date)}
	if company:
		filters["company"] = company
	rows = frappe.get_all("Employee", filters=filters, fields=["name", "relieving_date"])
	return [r.name for r in rows if not r.relieving_date or getdate(r.relieving_date) >= date]


def count_by_lg_code(date, company=None):
	"""指定日時点の住所の市区町村コードごとに在籍者を数える（EMP-05）。住所のない従業員は "" に数える。"""
	counter = Counter()
	for employee in active_employees(date, company):
		row = get_address_at(employee, date)
		counter[(row.lg_code or "") if row else ""] += 1
	return dict(counter)
