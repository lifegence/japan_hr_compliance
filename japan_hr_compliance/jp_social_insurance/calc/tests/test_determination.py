"""SIR-41〜45・48、6.7、8章 #6・#7。等級表は fixtures の例示値。"""

import unittest
from datetime import date
from decimal import Decimal as D

from .. import (
    DECREASE,
    GENERAL,
    INCREASE,
    PART_TIME_THREE_QUARTERS,
    PAY_DAILY,
    PAY_HOURLY,
    PAY_MONTHLY,
    SHORT_TIME,
    MonthlyRemuneration,
    Qualification,
    annual_average_assessment,
    is_regular_determination_target,
    judge_grade_change,
    leave_end_revision,
    occasional_revision,
    payment_base_days,
    regular_determination,
    select_months_by_base_days,
)
from .fixtures import HEALTH_TABLE, PENSION_TABLE


def m(y, mo):
    return date(y, mo, 1)


def q3(days, amounts=(300000, 300000, 300000), **kw):
    return [MonthlyRemuneration(m(2026, 4 + i), d, D(a), **kw) for i, (d, a) in enumerate(zip(days, amounts))]


class PaymentBaseDaysTest(unittest.TestCase):
    """SIR-41。"""

    def test_monthly_calendar_days(self):
        self.assertEqual(payment_base_days(PAY_MONTHLY, calendar_days=31), 31)

    def test_monthly_with_absence_deduction(self):
        self.assertEqual(
            payment_base_days(PAY_MONTHLY, has_absence_deduction=True, prescribed_days=21, absence_days=5), 16
        )

    def test_daily_hourly_attendance(self):
        self.assertEqual(payment_base_days(PAY_DAILY, attendance_days=18), 18)
        self.assertEqual(payment_base_days(PAY_HOURLY, attendance_days=12), 12)

    def test_missing_input(self):
        with self.assertRaises(ValueError):
            payment_base_days(PAY_MONTHLY)
        with self.assertRaises(ValueError):
            payment_base_days(PAY_DAILY)


class Example67Test(unittest.TestCase):
    """6.7 定時決定の支払基礎日数（ケースA〜E）。"""

    def months(self, days, category):
        return [r.month.month for r in select_months_by_base_days(q3(days), category)]

    def test_case_a(self):
        self.assertEqual(self.months((30, 31, 30), GENERAL), [4, 5, 6])

    def test_case_b(self):
        self.assertEqual(self.months((18, 16, 15), PART_TIME_THREE_QUARTERS), [4])

    def test_case_c(self):
        self.assertEqual(self.months((16, 16, 15), PART_TIME_THREE_QUARTERS), [4, 5, 6])

    def test_case_d(self):
        self.assertEqual(self.months((14, 16, 12), PART_TIME_THREE_QUARTERS), [5])

    def test_case_e(self):
        records = q3((12, 10, 11), (120000, 90000, 110000))
        r = regular_determination(2026, records, SHORT_TIME, HEALTH_TABLE, PENSION_TABLE)
        self.assertEqual([x.month for x in r.months_used], [4, 6])
        self.assertEqual(r.average, D("115000"))
        self.assertEqual(r.health.standard, D("118000"))
        self.assertEqual(r.pension.standard, D("118000"))
        self.assertEqual(r.effective_from, m(2026, 9))

    def test_general_under_17_not_counted(self):
        self.assertEqual(self.months((16, 17, 20), GENERAL), [5, 6])

    def test_all_months_below_uses_previous(self):
        r = regular_determination(2026, q3((10, 9, 8)), SHORT_TIME, HEALTH_TABLE, PENSION_TABLE)
        self.assertTrue(r.uses_previous)
        self.assertIsNone(r.health)


class RegularDeterminationAdjustmentTest(unittest.TestCase):
    """SIR-43・8章 #6：途中入社・遡及差額・休職月。"""

    def test_mid_month_entry_excluded(self):
        records = [
            MonthlyRemuneration(m(2026, 4), 17, D("150000"), excluded_reason="partial_month"),
            MonthlyRemuneration(m(2026, 5), 31, D("300000")),
            MonthlyRemuneration(m(2026, 6), 30, D("310000")),
        ]
        r = regular_determination(2026, records, GENERAL, HEALTH_TABLE, PENSION_TABLE)
        self.assertEqual(r.average, D("305000"))
        self.assertEqual(r.health.standard, D("300000"))

    def test_retroactive_pay_removed(self):
        records = [
            MonthlyRemuneration(m(2026, 4), 30, D("300000")),
            MonthlyRemuneration(m(2026, 5), 31, D("330000"), retroactive_pay=D("30000")),
            MonthlyRemuneration(m(2026, 6), 30, D("300000")),
        ]
        r = regular_determination(2026, records, GENERAL, HEALTH_TABLE, PENSION_TABLE)
        self.assertEqual(r.average, D("300000"))

    def test_in_kind_included_and_average_truncated(self):
        records = [
            MonthlyRemuneration(m(2026, 4), 30, D("300000"), in_kind=D("1")),
            MonthlyRemuneration(m(2026, 5), 31, D("300000")),
            MonthlyRemuneration(m(2026, 6), 30, D("300000")),
        ]
        r = regular_determination(2026, records, GENERAL, HEALTH_TABLE, None)
        self.assertEqual(r.average, D("300000"))  # 300,000.33… → 1円未満切捨て
        self.assertIsNone(r.pension)

    def test_rejects_out_of_period(self):
        with self.assertRaises(ValueError):
            regular_determination(
                2026, [MonthlyRemuneration(m(2026, 7), 31, D("1"))], GENERAL, HEALTH_TABLE, PENSION_TABLE
            )

    def test_target(self):
        q = Qualification(date(2020, 4, 1))
        self.assertTrue(is_regular_determination_target(2026, q))
        self.assertFalse(is_regular_determination_target(2026, Qualification(date(2026, 6, 1))))
        self.assertTrue(is_regular_determination_target(2026, Qualification(date(2026, 5, 31))))
        self.assertFalse(is_regular_determination_target(2026, q, [m(2026, 8)]))
        self.assertTrue(is_regular_determination_target(2026, q, [m(2026, 10)]))
        self.assertFalse(is_regular_determination_target(2026, Qualification(date(2020, 4, 1), date(2026, 7, 1))))


class OccasionalRevisionTest(unittest.TestCase):
    """SIR-44・45・8章 #7。"""

    def revise(self, amounts, *, direction, current, days=(31, 30, 31), category=GENERAL, current_pension=None, prev=None):
        records = [MonthlyRemuneration(m(2026, 7 + i), d, D(a)) for i, (d, a) in enumerate(zip(days, amounts))]
        return occasional_revision(
            m(2026, 7),
            records,
            category=category,
            direction=direction,
            health_table=HEALTH_TABLE,
            pension_table=PENSION_TABLE,
            current_health_standard=current,
            current_pension_standard=current if current_pension is None else current_pension,
            current_remuneration=prev,
        )

    def test_increase_two_grades(self):
        r = self.revise((345000, 345000, 345000), direction=INCREASE, current=300000)
        self.assertTrue(r.applicable)
        self.assertEqual(r.health.new.standard, D("340000"))
        self.assertEqual(r.health.grade_difference, 2)
        self.assertEqual(r.effective_from, m(2026, 10))  # 4か月目

    def test_decrease_two_grades(self):
        r = self.revise((255000, 255000, 255000), direction=DECREASE, current=300000)
        self.assertTrue(r.applicable)
        self.assertEqual(r.health.grade_difference, -2)

    def test_one_grade_not_applicable(self):
        r = self.revise((320000, 320000, 320000), direction=INCREASE, current=300000)
        self.assertFalse(r.applicable)

    def test_direction_mismatch(self):
        """固定的賃金が増えたのに平均が下がった場合（非固定の減少）は改定しない。"""
        r = self.revise((255000, 255000, 255000), direction=INCREASE, current=300000)
        self.assertFalse(r.applicable)

    def test_base_days_requirement(self):
        r = self.revise((345000,) * 3, direction=INCREASE, current=300000, days=(31, 16, 31))
        self.assertFalse(r.base_days_ok)
        self.assertFalse(r.applicable)
        r = self.revise((345000,) * 3, direction=INCREASE, current=300000, days=(11, 11, 12), category=SHORT_TIME)
        self.assertTrue(r.applicable)

    def test_health_only_when_pension_capped(self):
        """厚年が上限に張り付いている場合、健保のみ改定される（2.6）。"""
        r = self.revise((800000,) * 3, direction=INCREASE, current=680000, current_pension=650000)
        self.assertTrue(r.health.applicable)
        self.assertFalse(r.pension.applicable)
        self.assertTrue(r.applicable)

    def test_upper_special_health(self):
        """健保 49等級の者が報酬月額 1,415,000円以上 → 1等級差でも該当。"""
        j = judge_grade_change(HEALTH_TABLE, current_standard=1330000, average=1415000, direction=INCREASE)
        self.assertTrue(j.applicable)
        self.assertTrue(j.special_case)
        j = judge_grade_change(HEALTH_TABLE, current_standard=1330000, average=1414999, direction=INCREASE)
        self.assertFalse(j.applicable)

    def test_upper_special_pension(self):
        """厚年 31等級の者が 665,000円以上 → 該当。664,999円は非該当。"""
        j = judge_grade_change(PENSION_TABLE, current_standard=620000, average=665000, direction=INCREASE)
        self.assertTrue(j.applicable and j.special_case)
        j = judge_grade_change(PENSION_TABLE, current_standard=620000, average=664999, direction=INCREASE)
        self.assertFalse(j.applicable)

    def test_upper_special_decrease(self):
        j = judge_grade_change(
            PENSION_TABLE, current_standard=650000, average=630000, direction=DECREASE, current_remuneration=700000
        )
        self.assertTrue(j.applicable and j.special_case)
        j = judge_grade_change(
            PENSION_TABLE, current_standard=650000, average=630000, direction=DECREASE, current_remuneration=660000
        )
        self.assertFalse(j.applicable)

    def test_lower_special(self):
        j = judge_grade_change(HEALTH_TABLE, current_standard=68000, average=52999, direction=DECREASE)
        self.assertTrue(j.applicable and j.special_case)
        j = judge_grade_change(HEALTH_TABLE, current_standard=68000, average=53000, direction=DECREASE)
        self.assertFalse(j.applicable)
        j = judge_grade_change(PENSION_TABLE, current_standard=98000, average=82999, direction=DECREASE)
        self.assertTrue(j.applicable and j.special_case)
        j = judge_grade_change(
            PENSION_TABLE, current_standard=88000, average=95000, direction=INCREASE, current_remuneration=82000
        )
        self.assertTrue(j.applicable and j.special_case)
        j = judge_grade_change(
            PENSION_TABLE, current_standard=88000, average=95000, direction=INCREASE, current_remuneration=83000
        )
        self.assertFalse(j.applicable)

    def test_requires_three_consecutive_months(self):
        with self.assertRaises(ValueError):
            occasional_revision(
                m(2026, 7),
                [MonthlyRemuneration(m(2026, 7), 31, D("1"))],
                category=GENERAL,
                direction=INCREASE,
                health_table=HEALTH_TABLE,
                pension_table=None,
                current_health_standard=300000,
                current_pension_standard=None,
            )


class AnnualAverageTest(unittest.TestCase):
    """SIR-48：年間平均による保険者算定の候補。"""

    def setUp(self):
        # 4〜6月が繁忙期（月 400,000円）、他の月は 300,000円
        self.records = [
            MonthlyRemuneration(m(2025 + (mo < 7), mo), 30, D(400000 if mo in (4, 5, 6) else 300000))
            for mo in (7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6)
        ]
        self.regular = regular_determination(
            2026, [r for r in self.records if r.month.month in (4, 5, 6)], GENERAL, HEALTH_TABLE, PENSION_TABLE
        )

    def test_candidate(self):
        a = annual_average_assessment(
            2026,
            self.records,
            self.regular,
            category=GENERAL,
            health_table=HEALTH_TABLE,
            pension_table=PENSION_TABLE,
            seasonal_business_reason=True,
        )
        self.assertEqual(a.annual_average, D("325000"))
        self.assertEqual(self.regular.health.standard, D("410000"))
        self.assertEqual(a.health.standard, D("320000"))
        self.assertEqual(a.health_difference, -4)
        self.assertTrue(a.candidate)

    def test_not_candidate_without_seasonal_reason(self):
        a = annual_average_assessment(
            2026,
            self.records,
            self.regular,
            category=GENERAL,
            health_table=HEALTH_TABLE,
            pension_table=PENSION_TABLE,
            seasonal_business_reason=False,
        )
        self.assertFalse(a.candidate)


class LeaveEndRevisionTest(unittest.TestCase):
    """SIR-48：休業終了時改定（終了日の翌日が属する月以後3か月）。"""

    def test_childcare_end(self):
        records = [
            MonthlyRemuneration(m(2027, 4), 30, D("240000")),
            MonthlyRemuneration(m(2027, 5), 16, D("150000")),
            MonthlyRemuneration(m(2027, 6), 30, D("240000")),
        ]
        r = leave_end_revision(
            date(2027, 3, 31),
            records,
            category=GENERAL,
            health_table=HEALTH_TABLE,
            pension_table=PENSION_TABLE,
            current_health_standard=300000,
            current_pension_standard=300000,
        )
        self.assertEqual(r.months_considered, (m(2027, 4), m(2027, 5), m(2027, 6)))
        self.assertEqual(r.months_used, (m(2027, 4), m(2027, 6)))
        self.assertEqual(r.health.standard, D("240000"))
        self.assertTrue(r.health_changed and r.pension_changed)
        self.assertEqual(r.effective_from, m(2027, 7))

    def test_end_on_mid_month(self):
        records = [MonthlyRemuneration(m(2027, 3 + i), 20, D("300000")) for i in range(3)]
        r = leave_end_revision(
            date(2027, 3, 10),
            records,
            category=GENERAL,
            health_table=HEALTH_TABLE,
            pension_table=PENSION_TABLE,
            current_health_standard=300000,
        )
        self.assertFalse(r.health_changed)
        self.assertEqual(r.effective_from, m(2027, 6))


if __name__ == "__main__":
    unittest.main()
