"""健康保険料率の版（社会保険・雇用保険の計算規則 v1.0 SIR-01・02・06、5.3）。

協会けんぽの都道府県別の健康保険料率・介護保険料率（全国一律）・子ども・子育て支援金率を
適用開始月つきの版として持つ。料率は % で保持する（9.85% は 9.85）。
"""

import frappe
from frappe import _
from frappe.utils import flt

from japan_hr_compliance.jp_social_insurance import calc, masters
from japan_hr_compliance.jp_social_insurance import constants as C


class JPHealthInsuranceRate(masters.VersionedMaster):
	def validate_values(self):
		if self.insurer != C.INSURER_KYOKAI:
			frappe.throw(_("第1次の保険者は協会けんぽのみです（SIR-02）。"))
		seen = set()
		for row in self.rates:
			if row.prefecture in seen:
				frappe.throw(_("行 {0}：{1} が重複しています。").format(row.idx, row.prefecture))
			seen.add(row.prefecture)
			for field in ("health_rate", "care_rate", "support_rate"):
				if not 0 <= flt(row.get(field)) < 100:
					frappe.throw(_("行 {0}：料率は 0 以上 100 未満の % で入力してください。").format(row.idx))
			if flt(row.health_rate) <= 0:
				frappe.throw(_("行 {0}：健康保険料率を入力してください。").format(row.idx))
		if self.status == C.STATUS_CONFIRMED and not self.rates:
			frappe.throw(_("料率の行がない版は確認済にできません。"))

	@frappe.whitelist()
	def import_rates_csv(self, file_url=None, content=None):
		"""CSV（prefecture,health_rate,care_rate,support_rate。料率は %）で料率の行を置き換える。"""
		frappe.has_permission(self.doctype, "write", self.name, throw=True)
		rows = masters.read_csv(content if content is not None else masters.file_content(file_url))
		self.set("rates", [])
		for i, row in enumerate(rows, start=2):
			pref = row.get("prefecture") or row.get("都道府県")
			if pref not in C.PREFECTURES:
				frappe.throw(_("{0} 行目：都道府県が不正です（{1}）").format(i, pref))
			self.append("rates", {"prefecture": pref,
				"health_rate": float(masters._num(row, "health_rate", i) or 0),
				"care_rate": float(masters._num(row, "care_rate", i) or 0),
				"support_rate": float(masters._num(row, "support_rate", i) or 0)})
		self.save()
		return len(self.rates)

	@frappe.whitelist()
	def reconcile_premium_table(self, file_url=None, content=None):
		"""保険料額表の折半額と全等級の計算結果を突合し、結果を版に添付する（5.3、8章 #1）。

		CSV の列：prefecture,grade,health_half,health_care_half,support_half（折半額は銭まで）。
		健保（介護非該当）・健保＋介護・支援金をそれぞれ、適用開始月の健保の等級表で突合する。
		"""
		frappe.has_permission(self.doctype, "write", self.name, throw=True)
		rows = masters.read_csv(content if content is not None else masters.file_content(file_url))
		table = masters.select_grade_table(C.TABLE_HEALTH, self.effective_from, include_draft=True)
		by_pref = {}
		for i, row in enumerate(rows, start=2):
			pref = row.get("prefecture") or row.get("都道府県")
			grade = int(masters._num(row, "grade", i))
			bucket = by_pref.setdefault(pref, {"health": {}, "health_care": {}, "support": {}})
			for key, col in (("health", "health_half"), ("health_care", "health_care_half"), ("support", "support_half")):
				value = masters._num(row, col, i)
				if value is not None:
					bucket[key][grade] = value
		rates = {r.prefecture: r for r in self.rates}
		total_mismatches = 0
		for pref, bucket in by_pref.items():
			if pref not in rates:
				frappe.throw(_("{0} の料率がこの版にありません。").format(pref))
			r = rates[pref]
			checks = [("健保（介護非該当）", masters.pct(r.health_rate), bucket["health"]),
				("健保＋介護", masters.pct(r.health_rate) + masters.pct(r.care_rate), bucket["health_care"])]
			if flt(r.support_rate):
				checks.append(("子ども・子育て支援金", masters.pct(r.support_rate), bucket["support"]))
			for item, rate, expected in checks:
				if not expected:
					continue
				mismatches = calc.reconcile_premium_table(table.version, rate, expected)
				total_mismatches += len(mismatches)
				masters.append_reconciliation(self, item, pref, table.name, mismatches, len(table.version.grades))
		self.save()
		return total_mismatches
