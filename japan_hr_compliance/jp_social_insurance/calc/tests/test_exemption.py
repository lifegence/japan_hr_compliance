"""SIR-50〜52、6.6（育休A〜F・産休E）、8章 #8・#14。"""

import unittest
from datetime import date
from decimal import Decimal as D

from .. import (
    CHILDCARE,
    MATERNITY,
    POSTNATAL_CHILDCARE,
    LeavePeriod,
    calculate_employment_insurance,
    calculate_monthly_premium,
    is_bonus_exempt,
    merge_continuous,
    monthly_exemption_months,
    one_month_expiry,
)
from .fixtures import EI_2026, PENSION_RATES, RATES_2026_04


def m(y, mo):
    return date(y, mo, 1)


def months(periods):
    return list(monthly_exemption_months(periods))


class Example66Test(unittest.TestCase):
    def test_a_14_days_same_month(self):
        p = [LeavePeriod(date(2026, 11, 5), date(2026, 11, 18), CHILDCARE)]
        self.assertEqual(months(p), [m(2026, 11)])

    def test_b_13_days(self):
        p = [LeavePeriod(date(2026, 11, 5), date(2026, 11, 17), CHILDCARE)]
        self.assertEqual(months(p), [])

    def test_c_across_months(self):
        p = [LeavePeriod(date(2026, 11, 20), date(2026, 12, 15), CHILDCARE)]
        self.assertEqual(months(p), [m(2026, 11)])

    def test_d_exactly_one_month_with_bonus(self):
        p = [LeavePeriod(date(2026, 12, 1), date(2026, 12, 31), CHILDCARE)]
        self.assertEqual(months(p), [m(2026, 12)])
        self.assertFalse(is_bonus_exempt(date(2026, 12, 10), p))

    def test_e_maternity_ends_dec_30(self):
        p = [LeavePeriod(date(2026, 10, 20), date(2026, 12, 30), MATERNITY)]
        self.assertEqual(months(p), [m(2026, 10), m(2026, 11)])

    def test_e_maternity_has_no_14_day_rule(self):
        p = [LeavePeriod(date(2026, 11, 5), date(2026, 11, 25), MATERNITY)]
        self.assertEqual(months(p), [])

    def test_f_two_continuous_applications(self):
        p = [
            LeavePeriod(date(2026, 11, 5), date(2026, 11, 10), CHILDCARE),
            LeavePeriod(date(2026, 11, 11), date(2026, 11, 20), CHILDCARE),
        ]
        blocks = merge_continuous(p)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].leave_days(), 16)
        self.assertEqual(months(p), [m(2026, 11)])


class ExemptionRulesTest(unittest.TestCase):
    def test_return_one_day_before_month_end(self):
        """月末1日前の復帰：11月30日に復帰（休業は11月29日まで）→ 11月は月末在休でない。"""
        p = [LeavePeriod(date(2026, 10, 1), date(2026, 11, 29), CHILDCARE)]
        self.assertEqual(months(p), [m(2026, 10)])

    def test_next_month_14_days_does_not_apply(self):
        p = [LeavePeriod(date(2026, 11, 25), date(2026, 12, 20), CHILDCARE)]
        self.assertEqual(months(p), [m(2026, 11)])

    def test_multiple_leaves_same_month_summed(self):
        p = [
            LeavePeriod(date(2026, 11, 2), date(2026, 11, 8), CHILDCARE),
            LeavePeriod(date(2026, 11, 16), date(2026, 11, 22), POSTNATAL_CHILDCARE),
        ]
        self.assertEqual(months(p), [m(2026, 11)])  # 7日＋7日

    def test_postnatal_work_days_excluded(self):
        p = [LeavePeriod(date(2026, 11, 5), date(2026, 11, 18), POSTNATAL_CHILDCARE, (date(2026, 11, 10),))]
        self.assertEqual(months(p), [])  # 14日 − 就業1日 = 13日

    def test_end_on_last_day_of_month(self):
        p = [LeavePeriod(date(2026, 11, 20), date(2026, 11, 30), CHILDCARE)]
        self.assertEqual(months(p), [m(2026, 11)])

    def test_maternity_then_childcare_combined(self):
        """産休の終了後に続けて育休を取得する場合は通算して月末在休を判定する。"""
        p = [
            LeavePeriod(date(2026, 9, 1), date(2026, 11, 30), MATERNITY),
            LeavePeriod(date(2026, 12, 1), date(2027, 3, 15), CHILDCARE),
        ]
        self.assertEqual(months(p), [m(2026, 9), m(2026, 10), m(2026, 11), m(2026, 12), m(2027, 1), m(2027, 2)])

    def test_invalid_period(self):
        with self.assertRaises(ValueError):
            LeavePeriod(date(2026, 11, 5), date(2026, 11, 4), CHILDCARE)
        with self.assertRaises(ValueError):
            LeavePeriod(date(2026, 11, 5), date(2026, 11, 6), "sick")


class BonusExemptionTest(unittest.TestCase):
    """SIR-51。"""

    def test_childcare_over_one_month(self):
        p = [LeavePeriod(date(2026, 12, 1), date(2027, 1, 1), CHILDCARE)]
        self.assertTrue(is_bonus_exempt(date(2026, 12, 10), p))

    def test_childcare_continuous_applications_over_one_month(self):
        p = [
            LeavePeriod(date(2026, 12, 1), date(2026, 12, 15), CHILDCARE),
            LeavePeriod(date(2026, 12, 16), date(2027, 1, 10), CHILDCARE),
        ]
        self.assertTrue(is_bonus_exempt(date(2026, 12, 10), p))

    def test_childcare_not_covering_month_end(self):
        p = [LeavePeriod(date(2026, 11, 1), date(2026, 12, 20), CHILDCARE)]
        self.assertFalse(is_bonus_exempt(date(2026, 12, 10), p))

    def test_maternity_in_exempt_month(self):
        p = [LeavePeriod(date(2026, 11, 20), date(2027, 1, 15), MATERNITY)]
        self.assertTrue(is_bonus_exempt(date(2026, 12, 10), p))
        p2 = [LeavePeriod(date(2026, 10, 20), date(2026, 12, 30), MATERNITY)]
        self.assertFalse(is_bonus_exempt(date(2026, 12, 10), p2))  # 産休E：12月は免除月でない

    def test_one_month_expiry(self):
        self.assertEqual(one_month_expiry(date(2026, 12, 1)), date(2026, 12, 31))
        self.assertEqual(one_month_expiry(date(2026, 1, 10)), date(2026, 2, 9))
        self.assertEqual(one_month_expiry(date(2027, 1, 31)), date(2027, 2, 28))


class ExemptionScopeTest(unittest.TestCase):
    """SIR-52：免除月は健保・介護・支援金・厚年・拠出金が0円、雇用保険料は免除されない。"""

    def test_exempt_month_all_zero(self):
        p = calculate_monthly_premium(
            m(2026, 11),
            birth_date=date(1981, 5, 10),
            health_standard=300000,
            pension_standard=300000,
            health_rates=RATES_2026_04,
            pension_rates=PENSION_RATES,
            exempt=True,
        )
        self.assertTrue(p.exempt)
        self.assertEqual(p.employee_total, D("0"))
        self.assertEqual(p.employer_total, D("0"))
        self.assertIsNone(p.child_contribution)

    def test_employment_insurance_not_exempt(self):
        e = calculate_employment_insurance(D("100000"), EI_2026)
        self.assertEqual(e.employee, D("500"))
        self.assertEqual(calculate_employment_insurance(D("0"), EI_2026).employee, D("0"))


if __name__ == "__main__":
    unittest.main()
