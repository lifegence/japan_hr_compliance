"""SIR-60〜63・70〜73、6.8、8章 #9・#10。料率・判定条件は fixtures／ここでの例示値。"""

import unittest
from datetime import date
from decimal import Decimal as D

from .. import (
    BASIS_PAYMENT_DATE,
    COVERAGE_GENERAL,
    COVERAGE_NONE,
    COVERAGE_SHORT_TIME,
    EligibilityRule,
    calculate_employment_insurance,
    classify_employment_insurance,
    classify_social_insurance,
    employment_insurance_target_wage,
    headcount_warning,
    select_employment_insurance_rate,
)
from .fixtures import EI_2025, EI_2026, EI_GENERAL, EI_VERSIONS


class Example68Test(unittest.TestCase):
    def test_263500(self):
        e = calculate_employment_insurance(D("263500"), EI_2026)
        self.assertEqual(e.employee, D("1317"))  # 1,317.50 → 50銭ちょうどは切捨て
        self.assertEqual(e.employer, D("263500") * D("8.5") / 1000)


class EmploymentInsuranceTest(unittest.TestCase):
    def test_rate_switch_closing_date(self):
        """SIR-60：3月締め4月払いは旧料率、4月締め4月払いは新料率（既定：賃金締切日）。"""
        v = select_employment_insurance_rate(
            EI_VERSIONS, EI_GENERAL, closing_date=date(2026, 3, 31), payment_date=date(2026, 4, 25)
        )
        self.assertIs(v, EI_2025)
        v = select_employment_insurance_rate(
            EI_VERSIONS, EI_GENERAL, closing_date=date(2026, 4, 15), payment_date=date(2026, 4, 25)
        )
        self.assertIs(v, EI_2026)

    def test_rate_switch_payment_date(self):
        v = select_employment_insurance_rate(
            EI_VERSIONS,
            EI_GENERAL,
            closing_date=date(2026, 3, 31),
            payment_date=date(2026, 4, 25),
            basis=BASIS_PAYMENT_DATE,
        )
        self.assertIs(v, EI_2026)

    def test_bonus_uses_payment_date(self):
        v = select_employment_insurance_rate(
            EI_VERSIONS, EI_GENERAL, closing_date=None, payment_date=date(2026, 4, 1), is_bonus=True
        )
        self.assertIs(v, EI_2026)

    def test_business_type_filter(self):
        with self.assertRaises(LookupError):
            select_employment_insurance_rate(
                EI_VERSIONS, "construction", closing_date=date(2026, 4, 30), payment_date=date(2026, 4, 30)
            )

    def test_officer_and_non_insured(self):
        """SIR-61：役員・被保険者でない者は計算しない。"""
        self.assertEqual(calculate_employment_insurance(D("500000"), EI_2026, is_officer=True).employee, D("0"))
        self.assertEqual(calculate_employment_insurance(D("500000"), EI_2026, insured=False).employee, D("0"))

    def test_no_age_exemption(self):
        """SIR-61：65歳以上の被保険者にも保険料が計算される（年齢の入力自体がない）。"""
        self.assertEqual(calculate_employment_insurance(D("200000"), EI_2026).employee, D("1000"))

    def test_target_wage_excludes_non_target_items(self):
        """SIR-62：通勤手当は対象、退職金・慶弔見舞金は対象外。"""
        wage = employment_insurance_target_wage(
            [(D("250000"), True), (D("13500"), True), (D("1000000"), False), (D("10000"), False)]
        )
        self.assertEqual(wage, D("263500"))


RULES = [
    # 例示値：賃金要件 8.8万円あり（2024年10月〜）、撤廃（2026年10月〜）
    EligibilityRule(date(2024, 10, 1), True, D("88000"), 51, D("20")),
    EligibilityRule(date(2026, 10, 1), False, None, 51, D("20")),
]


def classify(judgment_date, **kw):
    args = dict(
        weekly_hours=24,
        monthly_days=16,
        normal_weekly_hours=40,
        normal_monthly_days=20,
        monthly_wage=80000,
        expected_over_two_months=True,
        is_student=False,
        specific_applicable_office=True,
    )
    args.update(kw)
    return classify_social_insurance(judgment_date, RULES, **args)


class ShortTimeEligibilityTest(unittest.TestCase):
    """SIR-70〜73・8章 #10。"""

    def test_wage_requirement_before_and_after_enforcement(self):
        self.assertEqual(classify(date(2026, 9, 30)), COVERAGE_NONE)
        self.assertEqual(classify(date(2026, 10, 1)), COVERAGE_SHORT_TIME)
        self.assertEqual(classify(date(2026, 9, 30), monthly_wage=88000), COVERAGE_SHORT_TIME)

    def test_three_quarters_boundary(self):
        self.assertEqual(classify(date(2026, 10, 1), weekly_hours=30, monthly_days=15), COVERAGE_GENERAL)
        self.assertEqual(classify(date(2026, 10, 1), weekly_hours=D("29.5"), monthly_days=15), COVERAGE_SHORT_TIME)
        self.assertEqual(classify(date(2026, 10, 1), weekly_hours=30, monthly_days=14), COVERAGE_SHORT_TIME)

    def test_20_hours_boundary(self):
        self.assertEqual(classify(date(2026, 10, 1), weekly_hours=20), COVERAGE_SHORT_TIME)
        self.assertEqual(classify(date(2026, 10, 1), weekly_hours=D("19.75")), COVERAGE_NONE)

    def test_other_requirements(self):
        self.assertEqual(classify(date(2026, 10, 1), is_student=True), COVERAGE_NONE)
        self.assertEqual(classify(date(2026, 10, 1), expected_over_two_months=False), COVERAGE_NONE)
        self.assertEqual(classify(date(2026, 10, 1), specific_applicable_office=False), COVERAGE_NONE)

    def test_enforcement_date_can_be_corrected(self):
        """施行日を後から修正できる（版の差し替え）。"""
        corrected = [RULES[0], EligibilityRule(date(2026, 11, 1), False, None, 51, D("20"))]
        self.assertEqual(
            classify_social_insurance(
                date(2026, 10, 15),
                corrected,
                weekly_hours=24,
                monthly_days=16,
                normal_weekly_hours=40,
                normal_monthly_days=20,
                monthly_wage=80000,
                expected_over_two_months=True,
                is_student=False,
                specific_applicable_office=True,
            ),
            COVERAGE_NONE,
        )

    def test_employment_insurance_20_hours(self):
        """SIR-73：週20時間の境界で区分が変わる。"""
        kw = dict(expected_31_days_or_more=True, excluded_student=False)
        self.assertTrue(classify_employment_insurance(date(2026, 11, 1), RULES, weekly_hours=20, **kw))
        self.assertFalse(classify_employment_insurance(date(2026, 11, 1), RULES, weekly_hours=D("19.5"), **kw))
        self.assertFalse(
            classify_employment_insurance(
                date(2026, 11, 1), RULES, weekly_hours=30, expected_31_days_or_more=False, excluded_student=False
            )
        )

    def test_headcount_warning(self):
        counts = {date(2026, mo, 1): (55 if mo >= 6 else 45) for mo in range(1, 12)}
        self.assertTrue(headcount_warning(counts, 51, date(2026, 11, 1)))  # 6〜11月の6か月
        self.assertFalse(headcount_warning(counts, 51, date(2026, 10, 1)))  # 5か月


if __name__ == "__main__":
    unittest.main()
