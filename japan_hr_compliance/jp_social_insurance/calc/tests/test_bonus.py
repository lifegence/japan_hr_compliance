"""SIR-30〜33、6.5、8章 #5・#13。料率・上限は fixtures の例示値。"""

import unittest
from datetime import date
from decimal import Decimal as D

from .. import (
    TREATMENT_LOSS_MONTH,
    TREATMENT_NOT_COVERED,
    TREATMENT_PREMIUM,
    Qualification,
    bonus_frequency_warning,
    calculate_bonus_premium,
    cap_pension_bonus,
    counts_as_remuneration,
    health_bonus_cumulative,
    loss_date_for_retirement,
    select_version,
    standard_bonus_amount,
)
from .fixtures import BONUS_CAPS, HEALTH_VERSIONS, PENSION_RATES, TOKYO

BIRTH_45 = date(1981, 5, 10)
QUAL = Qualification(date(2015, 4, 1))


def bonus(pay_date, amount, *, cumulative=0, same_month=0, hq=QUAL, pq=QUAL, exempt=False, birth=BIRTH_45):
    rates = select_version(HEALTH_VERSIONS, pay_date).rates_for(TOKYO)  # 支給月で版を選ぶ（5.2）
    return calculate_bonus_premium(
        pay_date,
        amount,
        birth_date=birth,
        health_rates=rates,
        pension_rates=PENSION_RATES,
        caps=BONUS_CAPS,
        health_qualification=hq,
        pension_qualification=pq,
        health_cumulative_before=cumulative,
        pension_same_month_before=same_month,
        exempt=exempt,
    )


class StandardBonusTest(unittest.TestCase):
    def test_floor_thousand(self):
        self.assertEqual(standard_bonus_amount(3200456), D("3200000"))
        self.assertEqual(standard_bonus_amount(999), D("0"))
        self.assertEqual(standard_bonus_amount(D("1000.99")), D("1000"))


class Example65Test(unittest.TestCase):
    """6.5 賞与の上限（年度内2回）。"""

    def test_first_bonus_june(self):
        b = bonus(date(2026, 6, 30), 3000000)
        self.assertEqual(b.standard_bonus, D("3000000"))
        self.assertEqual(b.health_standard_bonus, D("3000000"))
        self.assertEqual(b.health_cumulative_after, D("3000000"))
        self.assertEqual(b.pension_standard_bonus, D("1500000"))

    def test_second_bonus_december(self):
        first = bonus(date(2026, 6, 30), 3000000)
        cumulative = health_bonus_cumulative([(first.pay_date, first.health_standard_bonus)], date(2026, 12, 10))
        b = bonus(date(2026, 12, 10), 3200456, cumulative=cumulative)
        self.assertEqual(b.standard_bonus, D("3200000"))
        self.assertEqual(b.health_cumulative_after, D("5730000"))
        self.assertEqual(b.health_standard_bonus, D("2730000"))  # SIR-31 受入基準
        self.assertEqual(b.pension_standard_bonus, D("1500000"))
        self.assertEqual(b.health_care.combined.total / 2, D("156565.5"))
        self.assertEqual(b.health_care.combined.employee, D("156565"))
        self.assertEqual(b.support.employee, D("3139"))
        self.assertEqual(b.pension.employee, D("137250"))
        self.assertEqual(b.child_contribution.employer, D("5400"))  # 1,500,000 × 0.36%

    def test_cumulative_resets_on_april_first(self):
        prior = [(date(2026, 6, 30), D("3000000")), (date(2026, 12, 10), D("2730000"))]
        self.assertEqual(health_bonus_cumulative(prior, date(2027, 3, 31)), D("5730000"))
        self.assertEqual(health_bonus_cumulative(prior, date(2027, 4, 1)), D("0"))
        b = bonus(date(2027, 3, 31), 1000000, cumulative=D("5730000"))
        self.assertEqual(b.health_standard_bonus, D("0"))

    def test_prior_employer_cumulative(self):
        """前職（協会けんぽ）の累計を手入力できる。"""
        c = health_bonus_cumulative([], date(2026, 12, 10), prior_employer=D("5000000"))
        b = bonus(date(2026, 12, 10), 1000000, cumulative=c)
        self.assertEqual(b.health_standard_bonus, D("730000"))


class PensionMonthlyCapTest(unittest.TestCase):
    def test_two_bonuses_same_month(self):
        """SIR-32：同じ月の2回の賞与で合計150万円に抑えられる。"""
        b1 = bonus(date(2026, 12, 5), 1000000)
        b2 = bonus(date(2026, 12, 20), 800000, same_month=b1.pension_standard_bonus)
        self.assertEqual(b1.pension_standard_bonus, D("1000000"))
        self.assertEqual(b2.pension_standard_bonus, D("500000"))
        self.assertEqual(b1.pension_standard_bonus + b2.pension_standard_bonus, D("1500000"))
        self.assertEqual(cap_pension_bonus(D("100000"), D("1500000"), D("1500000")), D("0"))


class LossMonthBonusTest(unittest.TestCase):
    """SIR-33・8章 #5・#13。"""

    def test_loss_month_bonus_no_premium_but_cumulative(self):
        q = Qualification(date(2015, 4, 1), loss_date_for_retirement(date(2026, 12, 30)))
        b = bonus(date(2026, 12, 10), 500000, cumulative=D("1000000"), hq=q, pq=q)
        self.assertEqual(b.health_treatment, TREATMENT_LOSS_MONTH)
        self.assertEqual(b.employee_total, D("0"))
        self.assertIsNone(b.child_contribution)
        self.assertEqual(b.health_cumulative_after, D("1500000"))
        self.assertTrue(b.report_required)

    def test_after_loss_not_covered(self):
        q = Qualification(date(2015, 4, 1), loss_date_for_retirement(date(2026, 12, 15)))
        b = bonus(date(2026, 12, 20), 500000, cumulative=D("1000000"), hq=q, pq=q)
        self.assertEqual(b.health_treatment, TREATMENT_NOT_COVERED)
        self.assertEqual(b.health_cumulative_after, D("1000000"))
        self.assertEqual(b.health_standard_bonus, D("0"))
        self.assertFalse(b.report_required)

    def test_same_month_gain_loss_bonus_has_premium(self):
        q = Qualification(date(2026, 11, 10), loss_date_for_retirement(date(2026, 11, 25)))
        b = bonus(date(2026, 11, 20), 300000, hq=q, pq=q)
        self.assertEqual(b.health_treatment, TREATMENT_PREMIUM)
        self.assertEqual(b.pension.employee, D("27450"))
        self.assertGreater(b.employee_total, 0)

    def test_month_end_retirement_bonus_in_retirement_month(self):
        q = Qualification(date(2015, 4, 1), loss_date_for_retirement(date(2026, 12, 31)))
        b = bonus(date(2026, 12, 10), 300000, hq=q, pq=q)
        self.assertEqual(b.health_treatment, TREATMENT_PREMIUM)

    def test_pension_70_attainment_month(self):
        """70歳到達月（到達日前）の賞与は厚年が喪失月扱いで保険料なし、健保は保険料あり。"""
        b = bonus(date(2026, 11, 10), 300000, birth=date(1956, 11, 20))  # 到達日 11/19
        after = bonus(date(2026, 11, 25), 300000, birth=date(1956, 11, 20))
        self.assertEqual(after.pension_treatment, TREATMENT_NOT_COVERED)
        self.assertEqual(b.pension_treatment, TREATMENT_LOSS_MONTH)
        self.assertIsNone(b.pension)
        self.assertIsNone(b.child_contribution)
        self.assertIsNotNone(b.health_care)

    def test_exempt_bonus(self):
        b = bonus(date(2026, 12, 10), 300000, exempt=True)
        self.assertEqual(b.employee_total, D("0"))
        self.assertIsNone(b.child_contribution)
        self.assertEqual(b.health_cumulative_after, D("300000"))


class BonusFrequencyTest(unittest.TestCase):
    def test_rule_four_or_more_is_remuneration(self):
        self.assertTrue(counts_as_remuneration(4))
        self.assertFalse(counts_as_remuneration(3))
        self.assertFalse(counts_as_remuneration(None))

    def test_actual_payments_warning(self):
        dates = [date(2025, 7, 2), date(2025, 10, 1), date(2026, 1, 1), date(2026, 7, 1)]
        self.assertTrue(bonus_frequency_warning(dates, date(2026, 7, 1)))
        self.assertFalse(bonus_frequency_warning([date(2025, 7, 1)] + dates[1:], date(2026, 7, 1)))
        with self.assertRaises(ValueError):
            bonus_frequency_warning(dates, date(2026, 6, 30))


if __name__ == "__main__":
    unittest.main()
