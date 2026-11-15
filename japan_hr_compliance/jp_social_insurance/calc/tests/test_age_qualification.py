"""SIR-13・24〜26、6.2〜6.4、8章 #3・#4・#12。料率は fixtures の例示値。"""

import unittest
from datetime import date
from decimal import Decimal as D

from .. import (
    CURRENT_MONTH,
    NEXT_MONTH,
    Qualification,
    StandardRemuneration,
    attainment_date,
    attainment_month,
    calculate_monthly_premium,
    calculate_pay_deduction,
    care_insurance_applies,
    health_applies_by_age,
    loss_date_for_retirement,
    pension_applies_by_age,
    plan_deduction,
)
from .fixtures import HEALTH_VERSIONS, PENSION_RATES, PENSION_VERSIONS, RATES_2026_04, TOKYO


def m(y, mo):
    return date(y, mo, 1)


class AgeAttainmentTest(unittest.TestCase):
    """6.2 40歳到達月・65歳到達月（誕生日1日・2日）。"""

    def test_40_born_on_1st(self):
        b = date(1986, 11, 1)
        self.assertEqual(attainment_date(b, 40), date(2026, 10, 31))
        self.assertFalse(care_insurance_applies(b, m(2026, 9)))
        self.assertTrue(care_insurance_applies(b, m(2026, 10)))

    def test_40_born_on_2nd(self):
        b = date(1986, 11, 2)
        self.assertEqual(attainment_date(b, 40), date(2026, 11, 1))
        self.assertFalse(care_insurance_applies(b, m(2026, 10)))
        self.assertTrue(care_insurance_applies(b, m(2026, 11)))

    def test_65_born_on_1st(self):
        b = date(1961, 11, 1)
        self.assertTrue(care_insurance_applies(b, m(2026, 9)))
        self.assertFalse(care_insurance_applies(b, m(2026, 10)))

    def test_65_born_on_2nd(self):
        b = date(1961, 11, 2)
        self.assertTrue(care_insurance_applies(b, m(2026, 10)))
        self.assertFalse(care_insurance_applies(b, m(2026, 11)))

    def test_deduction_pay_month_next_month(self):
        """6.2：翌月控除では 11月支給分から（1日生まれ）・12月支給分から（2日生まれ）。"""
        qual = Qualification(date(2010, 4, 1))
        hist = [StandardRemuneration(date(2010, 4, 1), 300000, 300000)]

        def care_on(birth, pay_month, timing=NEXT_MONTH):
            d = calculate_pay_deduction(
                pay_month,
                timing=timing,
                birth_date=birth,
                prefecture=TOKYO,
                health_qualification=qual,
                pension_qualification=qual,
                standard_history=hist,
                health_rate_versions=HEALTH_VERSIONS,
                pension_rate_versions=PENSION_VERSIONS,
            )
            return d.premiums[0].care_applies

        self.assertFalse(care_on(date(1986, 11, 1), m(2026, 10)))
        self.assertTrue(care_on(date(1986, 11, 1), m(2026, 11)))
        self.assertFalse(care_on(date(1986, 11, 2), m(2026, 11)))
        self.assertTrue(care_on(date(1986, 11, 2), m(2026, 12)))
        self.assertTrue(care_on(date(1961, 11, 1), m(2026, 10)))  # 最後
        self.assertFalse(care_on(date(1961, 11, 1), m(2026, 11)))
        self.assertTrue(care_on(date(1961, 11, 2), m(2026, 11)))  # 最後
        self.assertFalse(care_on(date(1961, 11, 2), m(2026, 12)))
        # 当月控除では対象月と控除する給与の月が一致する
        self.assertTrue(care_on(date(1986, 11, 1), m(2026, 10), CURRENT_MONTH))

    def test_70_pension(self):
        """70歳到達（誕生日の前日）の前月分まで厚年・拠出金。"""
        b1 = date(1956, 11, 1)
        self.assertTrue(pension_applies_by_age(b1, m(2026, 9)))
        self.assertFalse(pension_applies_by_age(b1, m(2026, 10)))
        b2 = date(1956, 11, 2)
        self.assertTrue(pension_applies_by_age(b2, m(2026, 10)))
        self.assertFalse(pension_applies_by_age(b2, m(2026, 11)))
        p = calculate_monthly_premium(
            m(2026, 11),
            birth_date=b2,
            health_standard=300000,
            pension_standard=300000,
            health_rates=RATES_2026_04,
            pension_rates=PENSION_RATES,
        )
        self.assertIsNone(p.pension)
        self.assertIsNone(p.child_contribution)  # SIR-65
        self.assertIsNotNone(p.health_care)

    def test_75_health(self):
        """75歳到達（誕生日当日に喪失）の誕生月の前月分まで健保・介護・支援金。"""
        for b in (date(1951, 11, 1), date(1951, 11, 2)):
            self.assertTrue(health_applies_by_age(b, m(2026, 10)))
            self.assertFalse(health_applies_by_age(b, m(2026, 11)))
        p = calculate_monthly_premium(
            m(2026, 11),
            birth_date=date(1951, 11, 2),
            health_standard=300000,
            pension_standard=None,
            health_rates=RATES_2026_04,
            pension_rates=PENSION_RATES,
        )
        self.assertIsNone(p.health_care)
        self.assertIsNone(p.support)
        self.assertEqual(p.employee_total, D("0"))

    def test_leap_day_birth(self):
        self.assertEqual(attainment_date(date(1984, 2, 29), 41), date(2025, 2, 28))
        self.assertEqual(attainment_month(date(1984, 2, 29), 40), m(2024, 2))


class QualificationTest(unittest.TestCase):
    def test_premium_months(self):
        q = Qualification(date(2026, 4, 15), loss_date_for_retirement(date(2026, 12, 30)))
        self.assertFalse(q.is_premium_month(m(2026, 3)))
        self.assertTrue(q.is_premium_month(m(2026, 4)))  # 取得月は日割りなし
        self.assertTrue(q.is_premium_month(m(2026, 11)))
        self.assertFalse(q.is_premium_month(m(2026, 12)))  # 喪失月
        self.assertTrue(q.is_loss_month(m(2026, 12)))

    def test_same_month_gain_loss(self):
        q = Qualification(date(2026, 11, 10), loss_date_for_retirement(date(2026, 11, 25)))
        self.assertEqual(q.lost, date(2026, 11, 26))
        self.assertTrue(q.is_same_month_gain_loss)
        self.assertTrue(q.is_premium_month(m(2026, 11)))
        self.assertFalse(q.is_loss_month(m(2026, 11)))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            Qualification(date(2026, 1, 10), date(2026, 1, 10))


class DeductionPlanTest(unittest.TestCase):
    def test_next_month_default(self):
        """SIR-24：翌月控除で、3月分が4月支給の給与で控除される。"""
        q = Qualification(date(2020, 4, 1))
        self.assertEqual(plan_deduction(m(2026, 4), NEXT_MONTH, [q]).target_months, (m(2026, 3),))
        self.assertEqual(plan_deduction(m(2026, 4), CURRENT_MONTH, [q]).target_months, (m(2026, 4),))

    def test_first_pay_next_month(self):
        q = Qualification(date(2026, 11, 10))
        self.assertEqual(plan_deduction(m(2026, 11), NEXT_MONTH, [q]).target_months, ())
        self.assertEqual(plan_deduction(m(2026, 12), NEXT_MONTH, [q]).target_months, (m(2026, 11),))

    def test_63_month_end_retirement(self):
        """6.3：12月31日退職 → 12月25日支給の最終給与で11月分＋12月分。"""
        q = Qualification(date(2020, 4, 1), loss_date_for_retirement(date(2026, 12, 31)))
        self.assertEqual(q.last_premium_month, m(2026, 12))
        plan = plan_deduction(m(2026, 12), NEXT_MONTH, [q], is_final_pay=True)
        self.assertEqual(plan.target_months, (m(2026, 11), m(2026, 12)))
        cancelled = plan_deduction(m(2026, 12), NEXT_MONTH, [q], is_final_pay=True, deduct_two_months=False)
        self.assertEqual(cancelled.target_months, (m(2026, 11),))
        self.assertEqual(cancelled.deferred_months, (m(2026, 12),))

    def test_63_day_before_month_end(self):
        """6.3：12月30日退職 → 11月分のみ（2か月控除にならない）。"""
        q = Qualification(date(2020, 4, 1), loss_date_for_retirement(date(2026, 12, 30)))
        self.assertEqual(q.last_premium_month, m(2026, 11))
        plan = plan_deduction(m(2026, 12), NEXT_MONTH, [q], is_final_pay=True)
        self.assertEqual(plan.target_months, (m(2026, 11),))
        self.assertEqual(plan.deferred_months, ())

    def test_64_same_month_gain_loss(self):
        """6.4：11月10日入社・11月25日退職、11月支給の最終給与で11月分を控除。"""
        q = Qualification(date(2026, 11, 10), loss_date_for_retirement(date(2026, 11, 25)))
        plan = plan_deduction(m(2026, 11), NEXT_MONTH, [q], is_final_pay=True)
        self.assertEqual(plan.target_months, (m(2026, 11),))

    def test_current_month_final_pay(self):
        q = Qualification(date(2020, 4, 1), loss_date_for_retirement(date(2026, 12, 31)))
        plan = plan_deduction(m(2026, 12), CURRENT_MONTH, [q], is_final_pay=True)
        self.assertEqual(plan.target_months, (m(2026, 12),))


class TwoMonthDeductionWithRevisionTest(unittest.TestCase):
    """SIR-26・8章 #12：退職月が随時改定の適用月の月末退職者。"""

    def test_each_month_uses_its_own_standard(self):
        q = Qualification(date(2020, 4, 1), loss_date_for_retirement(date(2026, 12, 31)))
        history = [
            StandardRemuneration(date(2026, 9, 1), D("300000"), D("300000"), "regular"),
            StandardRemuneration(date(2026, 12, 1), D("360000"), D("360000"), "occasional"),
        ]
        d = calculate_pay_deduction(
            m(2026, 12),
            timing=NEXT_MONTH,
            birth_date=date(1981, 5, 10),
            prefecture=TOKYO,
            health_qualification=q,
            pension_qualification=q,
            standard_history=history,
            health_rate_versions=HEALTH_VERSIONS,
            pension_rate_versions=PENSION_VERSIONS,
            is_final_pay=True,
        )
        nov, dec = d.premiums
        self.assertEqual((nov.target_month, dec.target_month), (m(2026, 11), m(2026, 12)))
        self.assertEqual(nov.health_standard, D("300000"))
        self.assertEqual(dec.health_standard, D("360000"))
        # 健保＋介護 11.47%：300,000 → 17,205、360,000 → 20,646
        self.assertEqual(nov.health_care.combined.employee, D("17205"))
        self.assertEqual(dec.health_care.combined.employee, D("20646"))
        self.assertNotEqual(d.employee_total, nov.employee_total * 2)


if __name__ == "__main__":
    unittest.main()
