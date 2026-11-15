"""料率・等級表・上限・判定条件のマスタ（版）の管理と、計算エンジン用のデータへの変換
（社会保険・雇用保険の計算規則 v1.0 SIR-01〜06、5.1〜5.3）。

- 版の状態は「下書き → 確認済」。確認済にするには出典（資料名・公表日・URL）と確認者・確認日が必要
- 確認済の版だけを計算に使う（calc.select_version が status で絞る）
- 給与明細で使われた版は値を変更・削除できない。誤りは訂正版を登録し、元の版は
  「廃止（訂正済）」にする（状態と訂正版の名称だけは変更できる）
- 値はコードに持たない。CSV で取り込み、保険料額表と突合した結果を版に添付する
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, now_datetime

from japan_hr_compliance.jp_social_insurance import calc
from japan_hr_compliance.jp_social_insurance import constants as C

PREMIUM_LINE = "JP SI Premium Line"

# 版の DocType → 版の重複を検査する追加のキー（同じ適用開始で確認済が2件あると計算できない）
VERSION_KEYS = {
	"JP Health Insurance Rate": ("insurer",),
	"JP Pension Rate": (),
	"JP Standard Remuneration Table": ("kind",),
	"JP Bonus Cap": (),
	"JP Employment Insurance Rate": ("business_type",),
	"JP SI Eligibility Rule": (),
}
# 適用開始を「月」で扱う版（対象月の1日に直す）。雇用保険料率・判定条件は日で扱う
MONTHLY_VERSIONS = {"JP Health Insurance Rate", "JP Pension Rate", "JP Standard Remuneration Table", "JP Bonus Cap"}
# 使用後も変更できる項目
MUTABLE_AFTER_USE = {"status", "superseded_by", "remarks", "reconciliations", "modified", "modified_by"}


# ---------------------------------------------------------------------------
# 数値の変換
# ---------------------------------------------------------------------------


def dec(value) -> Decimal:
	"""Frappe の数値（float）を Decimal にする。二進誤差を持ち込まないよう文字列を経由する。"""
	if value in (None, ""):
		return Decimal("0")
	if isinstance(value, Decimal):
		return value
	if isinstance(value, int):
		return Decimal(value)
	return Decimal(repr(round(float(value), 9))).normalize()


def pct(value) -> Decimal:
	"""% で保持した料率を小数にする（9.85 → 0.0985）。"""
	return dec(value) / 100


def month_start(d) -> date:
	return getdate(d).replace(day=1)


# ---------------------------------------------------------------------------
# 版の検査（SIR-06、5.3）
# ---------------------------------------------------------------------------


def is_version_used(doctype: str, name: str) -> bool:
	"""確定済みの給与明細で使われた版か。

	等級表は、その版の有効期間に適用開始月がある確定済みの標準報酬月額の履歴（等級の検査と
	保険料計算の基礎）があれば使用済みとする。
	"""
	if doctype == "JP Standard Remuneration Table":
		return grade_table_used(name)
	filters = {"rate_doctype": doctype, "rate_version": name}
	return bool(frappe.db.exists(PREMIUM_LINE, {**filters, "parenttype": "Salary Slip", "docstatus": 1}))


def grade_table_used(name: str) -> bool:
	table = frappe.db.get_value("JP Standard Remuneration Table", name, ["kind", "effective_from", "status"], as_dict=True)
	if not table or table.status != C.STATUS_CONFIRMED:
		return False
	following = frappe.db.get_value("JP Standard Remuneration Table", {"kind": table.kind, "status": C.STATUS_CONFIRMED,
		"effective_from": (">", table.effective_from)}, "effective_from", order_by="effective_from asc")
	field = "health_standard" if table.kind == C.TABLE_HEALTH else "pension_standard"
	filters = {"docstatus": 1, field: (">", 0), "effective_from": (">=", table.effective_from)}
	if following:
		filters["effective_from"] = ("between", [table.effective_from, getdate(following) - timedelta(days=1)])
	return bool(frappe.db.exists("JP Standard Remuneration History", filters))


@frappe.whitelist()
def get_affected_salary_slips(doctype: str, name: str):
	"""版を使った給与明細の一覧（訂正版を登録したときの影響範囲。5.3）。

	等級表は給与明細では直接使わない（標準報酬月額の履歴を経由する）ため空の一覧を返す。
	"""
	frappe.has_permission(doctype, "read", name, throw=True)
	if doctype == "JP Standard Remuneration Table":
		return []
	filters = {"rate_version": name, "rate_doctype": doctype, "parenttype": "Salary Slip", "docstatus": ("<", 2)}
	parents = sorted(set(frappe.get_all(PREMIUM_LINE, filters=filters, pluck="parent")))
	if not parents:
		return []
	return frappe.get_all("Salary Slip", filters={"name": ("in", parents)},
		fields=["name", "employee", "employee_name", "posting_date", "docstatus"], order_by="posting_date, employee")


def _row_values(row, skip=("name", "parent", "parentfield", "parenttype", "idx", "creation", "modified", "modified_by",
		"owner", "docstatus", "doctype")):
	return tuple(sorted((k, str(v)) for k, v in row.as_dict().items() if k not in skip and not k.startswith("_")))


def changed_protected_fields(doc: Document) -> list[str]:
	"""使用後に変更できない項目のうち、保存前から変わったもの。"""
	before = doc.get_doc_before_save()
	if not before:
		return []
	changed = []
	for df in doc.meta.fields:
		if df.fieldname in MUTABLE_AFTER_USE or df.fieldtype in ("Section Break", "Column Break", "Tab Break"):
			continue
		if df.fieldtype == "Table":
			if [_row_values(r) for r in doc.get(df.fieldname)] != [_row_values(r) for r in before.get(df.fieldname)]:
				changed.append(df.label or df.fieldname)
		elif str(doc.get(df.fieldname) or "") != str(before.get(df.fieldname) or ""):
			if df.fieldtype in ("Float", "Currency", "Percent", "Int", "Check") and flt(doc.get(df.fieldname)) == flt(
				before.get(df.fieldname)
			):
				continue
			if df.fieldtype == "Date" and doc.get(df.fieldname) and before.get(df.fieldname) and getdate(
				doc.get(df.fieldname)
			) == getdate(before.get(df.fieldname)):
				continue
			changed.append(df.label or df.fieldname)
	return changed


def validate_confirmation(doc: Document, prefix: str = ""):
	"""確認済にする版の出典・確認者・確認日の入力を検査する（5.3）。"""
	if doc.status == C.STATUS_CONFIRMED:
		missing = [label for field, label in (("source_title", "資料名"), ("source_published", "公表日"),
			("source_url", "URL"), ("confirmed_by", "確認者"), ("confirmed_on", "確認日")) if not doc.get(field)]
		if missing:
			frappe.throw(_("{0}確認済にするには出典と確認者を入力してください：{1}").format(prefix, "、".join(missing)),
				title=_("確認済にできません"))
	if doc.status == C.STATUS_SUPERSEDED and doc.meta.has_field("superseded_by") and not doc.get("superseded_by"):
		frappe.throw(_("廃止（訂正済）にする場合は訂正版の名称を入力してください。"))


class VersionedMaster(Document):
	"""版のマスタの共通処理。サブクラスは validate_values() で値の検査を行う。"""

	def validate(self):
		if self.doctype in MONTHLY_VERSIONS and self.effective_from:
			self.effective_from = month_start(self.effective_from)
		self.validate_values()
		validate_confirmation(self)
		self.validate_unique_confirmed()
		self.validate_not_used()

	def validate_values(self):
		pass

	def validate_unique_confirmed(self):
		if self.status != C.STATUS_CONFIRMED:
			return
		filters = {"status": C.STATUS_CONFIRMED, "effective_from": self.effective_from, "name": ("!=", self.name)}
		for key in VERSION_KEYS.get(self.doctype, ()):
			filters[key] = self.get(key)
		other = frappe.db.get_value(self.doctype, filters, "name")
		if other:
			frappe.throw(_("同じ適用開始（{0}）の確認済の版 {1} があります。訂正する場合は {1} を「廃止（訂正済）」にしてください。")
				.format(self.effective_from, other), title=_("版の重複"))

	def validate_not_used(self):
		if self.is_new() or not is_version_used(self.doctype, self.name):
			return
		changed = changed_protected_fields(self)
		if changed:
			frappe.throw(_("この版は給与明細の計算に使われたため変更できません（{0}）。訂正版を登録してください。")
				.format("、".join(changed)), title=_("使用済みの版"))
		before = self.get_doc_before_save()
		if before and before.status == C.STATUS_CONFIRMED and self.status == C.STATUS_DRAFT:
			frappe.throw(_("給与明細で使われた版は下書きに戻せません。訂正版を登録してください。"), title=_("使用済みの版"))

	def on_trash(self):
		if is_version_used(self.doctype, self.name):
			frappe.throw(_("この版は給与明細の計算に使われたため削除できません。"), title=_("使用済みの版"))

	@frappe.whitelist()
	def get_affected_salary_slips(self):
		return get_affected_salary_slips(self.doctype, self.name)


# ---------------------------------------------------------------------------
# DB → 計算エンジンの版
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Named:
	"""計算エンジンの版と DB の名称の組。"""

	name: str
	version: object

	@property
	def effective_from(self):
		return self.version.effective_from

	@property
	def status(self):
		return self.version.status


def select_named(items: list[Named], target, *, by_month=True, predicate=None) -> Named:
	"""calc.select_version と同じ規則で版を選び、DB の名称と組にして返す。"""
	return calc.select_version(items, getdate(target), by_month=by_month,
		predicate=(lambda n: predicate(n.version)) if predicate else None)


def _calc_status(status):
	return C.STATUS_TO_CALC.get(status, "superseded")


def load_health_versions() -> list[Named]:
	rows = frappe.get_all("JP Health Insurance Rate Detail", filters={"parenttype": "JP Health Insurance Rate"},
		fields=["parent", "prefecture", "health_rate", "care_rate", "support_rate"])
	by_parent: dict[str, dict] = {}
	for r in rows:
		by_parent.setdefault(r.parent, {})[r.prefecture] = calc.HealthRates(pct(r.health_rate), pct(r.care_rate),
			pct(r.support_rate))
	out = []
	for v in frappe.get_all("JP Health Insurance Rate", fields=["name", "effective_from", "status"]):
		out.append(Named(v.name, calc.HealthInsuranceRateVersion(getdate(v.effective_from), by_parent.get(v.name, {}),
			status=_calc_status(v.status), name=v.name)))
	return out


def load_pension_versions() -> list[Named]:
	return [
		Named(v.name, calc.PensionRateVersion(getdate(v.effective_from),
			calc.PensionRates(pct(v.pension_rate), pct(v.child_contribution_rate)), status=_calc_status(v.status),
			name=v.name))
		for v in frappe.get_all("JP Pension Rate", fields=["name", "effective_from", "status", "pension_rate",
			"child_contribution_rate"])
	]


def build_grade_table(doc_or_name, *, any_status=False) -> calc.GradeTable:
	doc = frappe.get_doc("JP Standard Remuneration Table", doc_or_name) if isinstance(doc_or_name, str) else doc_or_name
	grades = [calc.Grade(cint(g.grade), dec(g.standard), dec(g.lower), dec(g.upper) if flt(g.upper) else None)
		for g in doc.grades]
	status = calc.CONFIRMED if any_status else _calc_status(doc.status)
	return calc.GradeTable(C.TABLE_KIND_TO_CALC[doc.kind], getdate(doc.effective_from), grades, status=status)


def load_grade_tables(kind: str) -> list[Named]:
	out = []
	for name in frappe.get_all("JP Standard Remuneration Table", filters={"kind": kind}, pluck="name"):
		out.append(Named(name, build_grade_table(name)))
	return out


def select_grade_table(kind: str, target_month, *, include_draft=False) -> Named:
	"""対象月の等級表。include_draft=True は突合用（確認前の版も候補に入れ、同月は確認済を優先）。"""
	if not include_draft:
		return select_named(load_grade_tables(kind), target_month)
	rows = frappe.get_all("JP Standard Remuneration Table",
		filters={"kind": kind, "status": ("!=", C.STATUS_SUPERSEDED), "effective_from": ("<=", month_start(target_month))},
		fields=["name", "status", "effective_from"], order_by="effective_from desc")
	if not rows:
		raise calc.VersionNotFoundError(_("{0} の{1}の等級表がありません").format(month_start(target_month), kind))
	latest = [r for r in rows if r.effective_from == rows[0].effective_from]
	latest.sort(key=lambda r: r.status != C.STATUS_CONFIRMED)
	return Named(latest[0].name, build_grade_table(latest[0].name, any_status=True))


def load_bonus_caps() -> list[Named]:
	return [
		Named(v.name, calc.BonusCapVersion(getdate(v.effective_from), dec(v.health_annual_cap), dec(v.pension_monthly_cap),
			status=_calc_status(v.status)))
		for v in frappe.get_all("JP Bonus Cap", fields=["name", "effective_from", "status", "health_annual_cap",
			"pension_monthly_cap"])
	]


def load_employment_rates() -> list[Named]:
	return [
		Named(v.name, calc.EmploymentInsuranceRateVersion(getdate(v.effective_from), v.business_type,
			dec(v.employee_permille), dec(v.employer_permille), status=_calc_status(v.status)))
		for v in frappe.get_all("JP Employment Insurance Rate", fields=["name", "effective_from", "status",
			"business_type", "employee_permille", "employer_permille"])
	]


@dataclass(frozen=True)
class WorkersCompVersion:
	effective_from: date
	permille: Decimal
	status: str

	@property
	def rate(self) -> Decimal:
		return self.permille / 1000


def load_workers_comp_versions(business_code: str) -> list[Named]:
	rows = frappe.get_all("JP Workers Comp Rate Version",
		filters={"parenttype": "JP Workers Comp Rate", "parent": business_code},
		fields=["name", "effective_from", "rate_permille", "status"])
	return [Named(r.name, WorkersCompVersion(getdate(r.effective_from), dec(r.rate_permille), _calc_status(r.status)))
		for r in rows]


def load_eligibility_rules() -> list[calc.EligibilityRule]:
	out = []
	for r in frappe.get_all("JP SI Eligibility Rule", fields=["effective_from", "status", "wage_requirement",
			"wage_threshold", "company_size_threshold", "sh_weekly_hours", "ei_weekly_hours"]):
		out.append(calc.EligibilityRule(
			getdate(r.effective_from), bool(r.wage_requirement), dec(r.wage_threshold) if r.wage_requirement else None,
			cint(r.company_size_threshold) or None, dec(r.ei_weekly_hours or 20), dec(r.sh_weekly_hours or 20),
			status=_calc_status(r.status)))
	return out


# ---------------------------------------------------------------------------
# CSV の取込みと保険料額表との突合（5.3）
# ---------------------------------------------------------------------------


def read_csv(content: str) -> list[dict]:
	"""見出し行つきの CSV を辞書の列にする（BOM・空行は無視）。"""
	content = content.lstrip("﻿")
	reader = csv.DictReader(io.StringIO(content))
	rows = []
	for row in reader:
		clean = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
		if any(clean.values()):
			rows.append(clean)
	return rows


def file_content(file_url: str) -> str:
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	content = file_doc.get_content()
	return content.decode("utf-8-sig") if isinstance(content, bytes) else content


def _num(row, key, line):
	value = row.get(key, "")
	if value == "":
		return None
	try:
		return dec(Decimal(value.replace(",", "")))
	except Exception:
		frappe.throw(_("{0} 行目：{1} の値が数値ではありません（{2}）").format(line, key, value))


def append_reconciliation(doc, item, prefecture, table_name, mismatches, checked):
	detail = "; ".join(
		f"{m.grade}等級 標準{m.standard:,.0f}円 額表{m.expected_half if m.expected_half is not None else 'なし'} "
		f"計算{m.calculated_half}" for m in mismatches[:20])
	if len(mismatches) > 20:
		detail += f" ほか{len(mismatches) - 20}件"
	doc.append("reconciliations", {"checked_on": now_datetime(), "prefecture": prefecture, "item": item,
		"grade_table": table_name, "grades_checked": checked, "mismatches": len(mismatches), "detail": detail})
