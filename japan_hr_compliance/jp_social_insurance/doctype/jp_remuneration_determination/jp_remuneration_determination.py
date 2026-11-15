"""標準報酬月額の決定処理：定時決定・随時改定・保険者算定（年間平均）・休業終了時改定（SIR-40〜48）。

月ごとの報酬は給与実績から集計し（「集計」ボタン／一括作成）、平均から除く理由は担当者が修正できる。
判定は計算エンジン（calc.determination）で行い、確定（Submit）で標準報酬月額の履歴
（JP Standard Remuneration History）に決定区分を付けて反映する。確定前は保険料計算に使われない。
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, cint, flt, getdate

from japan_hr_compliance.jp_social_insurance import calc, masters
from japan_hr_compliance.jp_social_insurance import constants as C
from japan_hr_compliance.jp_social_insurance import determination as det
from japan_hr_compliance.jp_social_insurance import employee_data as ed
from japan_hr_compliance.jp_social_insurance.masters import dec


class JPRemunerationDetermination(Document):
	def validate(self):
		self.validate_inputs()
		if not self.months:
			self.fill_months()
		self.run_calculation()

	def validate_inputs(self):
		t = self.determination_type
		if t in (C.DET_REGULAR, C.DET_ANNUAL) and not cint(self.year):
			frappe.throw(_("年を入力してください。"))
		if t == C.DET_OCCASIONAL:
			if not self.change_month or not self.direction:
				frappe.throw(_("随時改定は固定的賃金の変動月と変動の方向を入力してください。"))
			self.change_month = masters.month_start(self.change_month)
		if t in (C.DET_MATERNITY_END, C.DET_CHILDCARE_END) and not self.leave_end:
			frappe.throw(_("休業終了日を入力してください。"))
		if t == C.DET_ANNUAL and not self.regular_determination:
			frappe.throw(_("比較する定時決定を選んでください。"))

	@frappe.whitelist()
	def fill_months(self):
		"""給与実績から月ごとの報酬を集計して表を作り直す（SIR-40）。"""
		months = det.months_for(self)
		records = {r["pay_month"]: r for r in det.monthly_records(self.employee, months)}
		self.set("months", [])
		for m in months:
			rec = records.get(m)
			row = {"pay_month": m}
			if rec:
				row.update({k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in rec.items()})
			self.append("months", row)

	# ---- 判定 ----

	def _tables(self):
		month = det.effective_month(self)
		try:
			health = masters.select_grade_table(C.TABLE_HEALTH, month)
		except LookupError as e:
			frappe.throw(_("{0} の健保の等級表がありません：{1}").format(month, e))
		pension = None
		if ed.qualification_on(self.employee, C.INS_PENSION, month) or ed.qualification_on(
			self.employee, C.INS_PENSION, det.months_for(self)[-1]
		):
			try:
				pension = masters.select_grade_table(C.TABLE_PENSION, month)
			except LookupError as e:
				frappe.throw(_("{0} の厚年の等級表がありません：{1}").format(month, e))
		return month, health, pension

	def _set_grade(self, prefix, grade, current, table):
		self.set(f"new_{prefix}_standard", float(grade.standard) if grade else None)
		self.set(f"new_{prefix}_grade", grade.grade if grade else None)
		diff = None
		if grade and current and table:
			try:
				diff = grade.grade - table.by_standard(dec(current)).grade
			except LookupError:
				diff = None
		self.set(f"{prefix}_grade_diff", diff)

	def run_calculation(self, ignore_seasonal=False):
		month, health_t, pension_t = self._tables()
		self.effective_from = month
		current = ed.current_standard(self.employee, getdate(add_months(month, -1)))
		self.current_health_standard = float(current.health_standard) if current and current.health_standard else None
		self.current_pension_standard = float(current.pension_standard) if current and current.pension_standard else None
		records = det.to_calc_records(self)
		category = C.DAYS_TO_CALC[self.days_category or C.DAYS_GENERAL]
		h_table, p_table = health_t.version, pension_t.version if pension_t else None
		t = self.determination_type
		notes = []
		if t == C.DET_REGULAR:
			used = self._regular(records, category, h_table, p_table, current, notes)
		elif t == C.DET_OCCASIONAL:
			used = self._occasional(records, category, h_table, p_table, current, notes)
		elif t == C.DET_ANNUAL:
			used = self._annual(records, category, h_table, p_table, notes, ignore_seasonal)
		else:
			used = self._leave_end(records, category, h_table, p_table, current, notes)
		for row in self.months:
			row.included = 1 if masters.month_start(row.pay_month) in used else 0
		self.result_note = "\n".join(str(n) for n in notes)

	def _regular(self, records, category, h_table, p_table, current, notes):
		r = calc.regular_determination(cint(self.year), records, category, h_table, p_table)
		self._set_average(records, set(r.months_used))
		if r.uses_previous:
			notes.append(_("支払基礎日数の基準を満たす月がないため、従前の標準報酬月額で決定"))
			h = h_table.by_standard(dec(current.health_standard)) if current and current.health_standard else None
			p = p_table.by_standard(dec(current.pension_standard)) if (p_table and current and current.pension_standard) else None
		else:
			h, p = r.health, r.pension
		self._set_grade("health", h, self.current_health_standard, h_table)
		self._set_grade("pension", p, self.current_pension_standard, p_table)
		self.applicable = 1 if h else 0
		return set(r.months_used)

	def _occasional(self, records, category, h_table, p_table, current, notes):
		if not current or not current.health_standard:
			frappe.throw(_("従前の標準報酬月額（確定済み）がありません。"))
		try:
			r = calc.occasional_revision(self.change_month, records, category=category,
				direction=C.DIRECTION_TO_CALC[self.direction], health_table=h_table, pension_table=p_table,
				current_health_standard=current.health_standard, current_pension_standard=current.pension_standard,
				current_remuneration=dec(self.current_remuneration) if flt(self.current_remuneration) else None)
		except ValueError as e:
			frappe.throw(_("随時改定の判定ができません：{0}").format(e))
		used = {rec.month for rec in records}
		self._set_average(records, used)
		if not r.base_days_ok:
			notes.append(_("3か月のいずれかの支払基礎日数が基準未満"))
		for prefix, j in (("health", r.health), ("pension", r.pension)):
			if j is None:
				self._set_grade(prefix, None, None, None)
				continue
			grade = j.new if (r.base_days_ok and j.applicable) else j.current
			self.set(f"new_{prefix}_standard", float(grade.standard))
			self.set(f"new_{prefix}_grade", grade.grade)
			self.set(f"{prefix}_grade_diff", j.grade_difference)
			notes.append(_("{0}：{1}").format("健保" if prefix == "health" else "厚年", j.reason))
		self.applicable = 1 if r.applicable else 0
		return used

	def _annual(self, records, category, h_table, p_table, notes, ignore_seasonal):
		reg = frappe.get_doc(det.DOCTYPE, self.regular_determination)
		if reg.employee != self.employee or reg.determination_type != C.DET_REGULAR:
			frappe.throw(_("比較する定時決定が同じ従業員の定時決定ではありません。"))
		regular = calc.RegularDetermination(cint(reg.year), (),
			dec(reg.adjusted_average) if reg.adjusted_average else None,
			h_table.by_standard(dec(reg.new_health_standard)) if flt(reg.new_health_standard) else None,
			p_table.by_standard(dec(reg.new_pension_standard)) if (p_table and flt(reg.new_pension_standard)) else None,
			getdate(reg.effective_from))
		seasonal = True if ignore_seasonal else bool(cint(self.seasonal_reason))
		r = calc.annual_average_assessment(cint(self.year), records, regular, category=category, health_table=h_table,
			pension_table=p_table, seasonal_business_reason=seasonal)
		self._set_average(records, set(r.months_used))
		self._set_grade("health", r.health, None, None)
		self._set_grade("pension", r.pension, None, None)
		self.health_grade_diff, self.pension_grade_diff = r.health_difference, r.pension_difference
		self.applicable = 1 if r.candidate else 0
		notes.append(_("年間平均と定時決定の等級差 健保 {0}・厚年 {1}").format(r.health_difference, r.pension_difference))
		if not cint(self.seasonal_reason):
			notes.append(_("業務上の理由による季節的変動がなければ申立ての対象外"))
		return set(r.months_used)

	def _leave_end(self, records, category, h_table, p_table, current, notes):
		try:
			r = calc.leave_end_revision(getdate(self.leave_end), records, category=category, health_table=h_table,
				pension_table=p_table,
				current_health_standard=current.health_standard if current else None,
				current_pension_standard=current.pension_standard if current else None)
		except ValueError as e:
			frappe.throw(_("休業終了時改定の計算ができません：{0}").format(e))
		self._set_average(records, set(r.months_used))
		self._set_grade("health", r.health, self.current_health_standard, h_table)
		self._set_grade("pension", r.pension, self.current_pension_standard, p_table)
		self.applicable = 1 if (r.health_changed or r.pension_changed) else 0
		if not self.applicable:
			notes.append(_("従前の標準報酬月額と等級が変わらないため改定なし"))
		return set(r.months_used)

	def _set_average(self, records, used):
		rows = [r for r in records if r.month in used]
		if not rows:
			self.average = self.adjusted_average = None
			return
		self.average = float(sum((r.amount for r in rows), dec(0)) / len(rows))
		self.adjusted_average = float(calc.average_remuneration(rows))

	# ---- 確定（SIR-46） ----

	def before_submit(self):
		if self.determination_type == C.DET_ANNUAL and cint(self.filing_required):
			if not (cint(self.seasonal_reason) and cint(self.employee_consent)):
				frappe.throw(_("申立てを行う場合は、季節的変動の確認と本人の同意が必要です。"))
			if not cint(self.applicable):
				frappe.throw(_("定時決定との差が2等級未満のため申立ての対象になりません。"))

	def on_submit(self):
		if not cint(self.applicable):
			return
		if self.determination_type == C.DET_ANNUAL and not cint(self.filing_required):
			return
		if self.determination_type == C.DET_ANNUAL:
			# 保険者算定は同じ年の定時決定に代わる。定時決定から反映した履歴を取り消す
			replaced = frappe.db.get_value(det.DOCTYPE, self.regular_determination, "history")
			if replaced and frappe.db.get_value("JP Standard Remuneration History", replaced, "docstatus") == 1:
				old = frappe.get_doc("JP Standard Remuneration History", replaced)
				old.ignore_linked_doctypes = (det.DOCTYPE,)
				old.cancel()
		hist = frappe.get_doc({
			"doctype": "JP Standard Remuneration History", "employee": self.employee, "effective_from": self.effective_from,
			"decision_type": C.DETERMINATION_TO_DECISION[self.determination_type],
			"remuneration": self.adjusted_average, "health_standard": self.new_health_standard,
			"pension_standard": self.new_pension_standard, "determination": self.name,
		})
		hist.insert(ignore_permissions=True)
		hist.submit()
		self.db_set("history", hist.name)

	def before_cancel(self):
		# 反映した履歴は on_cancel で一緒に取り消す
		self.ignore_linked_doctypes = ("JP Standard Remuneration History",)

	def on_cancel(self):
		if self.history and frappe.db.get_value("JP Standard Remuneration History", self.history, "docstatus") == 1:
			frappe.get_doc("JP Standard Remuneration History", self.history).cancel()
