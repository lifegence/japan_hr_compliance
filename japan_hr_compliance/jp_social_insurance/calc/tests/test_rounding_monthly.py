"""SIR-20〜22・27、6.1、8章 #1・#2・#11・#15。料率は fixtures の例示値。"""

import unittest
from datetime import date
from decimal import Decimal as D

from .. import (
    CURRENT_MONTH,
    DISPLAY_COMBINED,
    DISPLAY_SEPARATE,
    NEXT_MONTH,
    Qualification,
    StandardRemuneration,
    calculate_monthly_premium,
    calculate_pay_deduction,
    reconcile_premium_table,
    reconcile_with_notice,
    round_50sen,
    split_health_and_care,
    split_premium,
    statement_lines,
)
from .fixtures import (
    HEALTH_TABLE,
    HEALTH_VERSIONS,
    PENSION_RATES,
    PENSION_TABLE,
    PENSION_VERSIONS,
    RATES_2026_04,
    TOKYO,
)

BIRTH_45 = date(1981, 5, 10)  # 2026年に45歳（介護該当）
BIRTH_39 = date(1987, 5, 10)  # 2026年に39歳（介護非該当）


class Round50SenTest(unittest.TestCase):
    def test_50sen_exactly_is_truncated(self):
        self.assertEqual(round_50sen(D("9749.50")), D("9749"))

    def test_over_50sen_is_rounded_up(self):
        self.assertEqual(round_50sen(D("9749.51")), D("9750"))
        self.assertEqual(round_50sen(D("9749.505")), D("9750"))

    def test_under_50sen_is_truncated(self):
        self.assertEqual(round_50sen(D("9749.49")), D("9749"))
        self.assertEqual(round_50sen(D("100")), D("100"))

    def test_rejects_float_and_negative(self):
        with self.assertRaises(TypeError):
            round_50sen(1.5)
        with self.assertRaises(ValueError):
            round_50sen(D("-1"))


class Example61Test(unittest.TestCase):
    """6.1 月次保険料の基本（標準報酬月額 170,000円）。"""

    def setUp(self):
        self.p = calculate_monthly_premium(
            date(2026, 4, 1),
            birth_date=BIRTH_45,
            health_standard=170000,
            pension_standard=170000,
            health_rates=RATES_2026_04,
            pension_rates=PENSION_RATES,
        )

    def test_health_and_care_combined(self):
        hc = self.p.health_care.combined
        self.assertEqual(hc.total, D("19499"))
        self.assertEqual(hc.half, D("9749.5"))
        self.assertEqual(hc.employee, D("9749"))

    def test_support_rounded_separately(self):
        self.assertEqual(self.p.support.total, D("391"))
        self.assertEqual(self.p.support.half, D("195.5"))
        self.assertEqual(self.p.support.employee, D("195"))

    def test_pension_and_child_contribution(self):
        self.assertEqual(self.p.pension.total, D("31110"))
        self.assertEqual(self.p.pension.employee, D("15555"))
        self.assertEqual(self.p.child_contribution.total, D("612"))
        self.assertEqual(self.p.child_contribution.employee, D("0"))
        self.assertEqual(self.p.child_contribution.employer, D("612"))

    def test_employee_total(self):
        self.assertEqual(self.p.employee_total, D("9749") + D("195") + D("15555"))

    def test_without_care_39(self):
        p = calculate_monthly_premium(
            date(2026, 4, 1),
            birth_date=BIRTH_39,
            health_standard=170000,
            pension_standard=170000,
            health_rates=RATES_2026_04,
            pension_rates=PENSION_RATES,
        )
        self.assertFalse(p.care_applies)
        self.assertIsNone(p.health_care.care)
        self.assertEqual(p.health_care.combined.total, D("16745"))
        self.assertEqual(p.health_care.combined.half, D("8372.5"))
        self.assertEqual(p.health_care.combined.employee, D("8372"))


class HealthCareAllocationTest(unittest.TestCase):
    """SIR-21：別行表示は合算額 − 健保のみの額で按分し、合計は合算額と一致する。"""

    def test_allocation_sums_to_combined(self):
        for g in HEALTH_TABLE.grades:
            b = split_health_and_care(g.standard, RATES_2026_04.health, RATES_2026_04.care)
            self.assertEqual(b.health.employee + b.care.employee, b.combined.employee)
            self.assertEqual(b.health.total + b.care.total, b.combined.total)
            self.assertEqual(b.health.employer + b.care.employer, b.combined.employer)

    def test_example_170000(self):
        b = split_health_and_care(170000, RATES_2026_04.health, RATES_2026_04.care)
        self.assertEqual(b.health.employee, D("8372"))
        self.assertEqual(b.care.employee, D("1377"))  # 9,749 − 8,372

    def test_statement_lines(self):
        p = calculate_monthly_premium(
            date(2026, 4, 1),
            birth_date=BIRTH_45,
            health_standard=170000,
            pension_standard=170000,
            health_rates=RATES_2026_04,
            pension_rates=PENSION_RATES,
        )
        default = {l.key: l.employee for l in statement_lines(p)}
        self.assertEqual(default, {"health_care": D("9749"), "support": D("195"), "pension": D("15555")})
        separate = {l.key: l.employee for l in statement_lines(p, care_display=DISPLAY_SEPARATE)}
        self.assertEqual(separate["health"] + separate["care"], D("9749"))
        merged = {l.key: l.employee for l in statement_lines(p, support_display=DISPLAY_COMBINED)}
        # 合算表示でも端数処理は別（9,749＋195）。8章 #15
        self.assertEqual(merged, {"health_care": D("9944"), "pension": D("15555")})


class EmployeeVsEmployerTest(unittest.TestCase):
    """2.2「本人負担が事業主負担を上回らない」の検算。

    折半額の端数が50銭を超えると切り上げになるため、事業主負担（全額−本人負担）を丸めずに
    比べると本人負担が上回る等級がある（PR の不明点に記載）。
    """

    def test_detects_employee_exceeding_employer(self):
        s = split_premium(142000, D("0.1147"))  # 全額 16,287.4、折半 8,143.70
        self.assertEqual(s.employee, D("8144"))
        self.assertEqual(s.employer, D("8143.4"))
        self.assertTrue(s.employee_exceeds_employer)

    def test_half_or_less_never_exceeds(self):
        s = split_premium(170000, D("0.1147"))
        self.assertFalse(s.employee_exceeds_employer)


class PremiumTableReconciliationTest(unittest.TestCase):
    """8章 #1：額表との全等級突合（ここでは額表を模した期待値で確認する）。"""

    def test_all_grades_match(self):
        rate = RATES_2026_04.health + RATES_2026_04.care
        expected = {g.grade: g.standard * rate / 2 for g in HEALTH_TABLE.grades}
        self.assertEqual(reconcile_premium_table(HEALTH_TABLE, rate, expected), [])

    def test_reports_mismatch_and_missing(self):
        rate = PENSION_RATES.pension
        expected = {g.grade: g.standard * rate / 2 for g in PENSION_TABLE.grades}
        expected[5] = expected[5] + 1
        del expected[32]
        mismatches = reconcile_premium_table(PENSION_TABLE, rate, expected)
        self.assertEqual([m.grade for m in mismatches], [5, 32])
        self.assertIsNone(mismatches[1].expected_half)


class NoticeReconciliationTest(unittest.TestCase):
    """SIR-27・8章 #11：個人別の事業主負担の合計と納入告知額の試算の差が端数の範囲。"""

    def test_difference_within_one_yen(self):
        rate = RATES_2026_04.health + RATES_2026_04.care
        shares = [split_premium(g.standard, rate) for g in HEALTH_TABLE.grades]
        r = reconcile_with_notice(shares)
        self.assertGreaterEqual(r.difference, 0)
        self.assertLess(r.difference, 1)
        self.assertEqual(r.employer_payable + r.employee_sum, r.notice_amount)


class RateVersionByTargetMonthTest(unittest.TestCase):
    """SIR-01・22・24、8章 #2：2月分・3月分・4月分（翌月控除・当月控除）。"""

    history = [StandardRemuneration(date(2025, 9, 1), D("300000"), D("300000"))]
    qual = Qualification(date(2020, 4, 1))

    def deduct(self, pay_month, timing):
        return calculate_pay_deduction(
            pay_month,
            timing=timing,
            birth_date=BIRTH_45,
            prefecture=TOKYO,
            health_qualification=self.qual,
            pension_qualification=self.qual,
            standard_history=self.history,
            health_rate_versions=HEALTH_VERSIONS,
            pension_rate_versions=PENSION_VERSIONS,
        )

    def test_next_month_deduction(self):
        feb = self.deduct(date(2026, 3, 1), NEXT_MONTH).premiums[0]
        mar = self.deduct(date(2026, 4, 1), NEXT_MONTH).premiums[0]
        apr = self.deduct(date(2026, 5, 1), NEXT_MONTH).premiums[0]
        self.assertEqual(feb.target_month, date(2026, 2, 1))
        # 2月分：令和7年度（9.91%＋1.59%）。300,000 × 11.50% ÷ 2 = 17,250
        self.assertEqual(feb.health_care.combined.employee, D("17250"))
        # 3月分（4月支給で控除）：新料率 11.47%、支援金なし。300,000 × 11.47% ÷ 2 = 17,205
        self.assertEqual(mar.target_month, date(2026, 3, 1))
        self.assertEqual(mar.health_care.combined.employee, D("17205"))
        self.assertIsNone(mar.support)
        # 4月分：支援金 300,000 × 0.23% ÷ 2 = 345。5月の下書きの版は使われない
        self.assertEqual(apr.support.employee, D("345"))
        self.assertEqual(apr.health_care.combined.employee, D("17205"))

    def test_current_month_deduction(self):
        apr = self.deduct(date(2026, 4, 1), CURRENT_MONTH).premiums[0]
        self.assertEqual(apr.target_month, date(2026, 4, 1))
        self.assertEqual(apr.support.employee, D("345"))
        mar = self.deduct(date(2026, 3, 1), CURRENT_MONTH).premiums[0]
        self.assertIsNone(mar.support)

    def test_draft_version_not_used(self):
        may = self.deduct(date(2026, 5, 1), CURRENT_MONTH).premiums[0]
        self.assertEqual(may.health_care.combined.employee, D("17205"))


if __name__ == "__main__":
    unittest.main()
