"""SIR-01〜03・06・12・46、5.2：版の選び方と等級表の当てはめ。"""

import unittest
from datetime import date
from decimal import Decimal as D

from .. import (
    DRAFT,
    AmbiguousVersionError,
    Grade,
    GradeTable,
    HealthInsuranceRateVersion,
    HealthRates,
    StandardRemuneration,
    VersionNotFoundError,
    select_standard_remuneration,
    select_version,
)
from .fixtures import (
    EI_VERSIONS,
    HEALTH_2025_03,
    HEALTH_2026_03,
    HEALTH_2026_04,
    HEALTH_TABLE,
    HEALTH_VERSIONS,
    PENSION_TABLE,
    TOKYO,
)


class SelectVersionTest(unittest.TestCase):
    def test_feb_mar_apr(self):
        """SIR-01 受入基準：2026年2月分・3月分・4月分でそれぞれの版。"""
        self.assertIs(select_version(HEALTH_VERSIONS, date(2026, 2, 1)), HEALTH_2025_03)
        self.assertIs(select_version(HEALTH_VERSIONS, date(2026, 3, 15)), HEALTH_2026_03)
        self.assertIs(select_version(HEALTH_VERSIONS, date(2026, 4, 30)), HEALTH_2026_04)

    def test_draft_ignored(self):
        """SIR-06：下書きの版は計算に使われない。"""
        self.assertIs(select_version(HEALTH_VERSIONS, date(2026, 6, 1)), HEALTH_2026_04)

    def test_no_version(self):
        with self.assertRaises(VersionNotFoundError):
            select_version(HEALTH_VERSIONS, date(2025, 2, 1))

    def test_only_draft_raises(self):
        draft = HealthInsuranceRateVersion(date(2024, 1, 1), {}, status=DRAFT)
        with self.assertRaises(VersionNotFoundError):
            select_version([draft], date(2024, 5, 1))

    def test_ambiguous(self):
        dup = HealthInsuranceRateVersion(date(2026, 4, 1), {TOKYO: HealthRates(D("0.1"), D("0.01"))})
        with self.assertRaises(AmbiguousVersionError):
            select_version(HEALTH_VERSIONS + [dup], date(2026, 4, 1))

    def test_prefecture_missing(self):
        with self.assertRaises(VersionNotFoundError):
            HEALTH_2026_04.rates_for("大阪府")

    def test_insurer_limited_to_kyokai(self):
        """SIR-02：健保組合は選べない。"""
        with self.assertRaises(ValueError):
            HealthInsuranceRateVersion(date(2026, 4, 1), {}, insurer="union")

    def test_day_based_selection(self):
        self.assertEqual(
            select_version(EI_VERSIONS, date(2026, 3, 31), by_month=False).effective_from,
            date(2025, 4, 1),
        )
        self.assertEqual(
            select_version(EI_VERSIONS, date(2026, 4, 1), by_month=False).effective_from,
            date(2026, 4, 1),
        )


class StandardRemunerationHistoryTest(unittest.TestCase):
    def test_selects_latest_confirmed(self):
        history = [
            StandardRemuneration(date(2025, 9, 1), 300000, 300000, "regular"),
            StandardRemuneration(date(2026, 1, 1), 360000, 360000, "occasional"),
            StandardRemuneration(date(2026, 3, 1), 410000, 410000, "occasional", confirmed=False),
        ]
        self.assertEqual(select_standard_remuneration(history, date(2025, 12, 1)).health_standard, D("300000"))
        self.assertEqual(select_standard_remuneration(history, date(2026, 1, 1)).health_standard, D("360000"))
        # SIR-46：確定前は保険料計算に使われない
        self.assertEqual(select_standard_remuneration(history, date(2026, 4, 1)).health_standard, D("360000"))
        with self.assertRaises(VersionNotFoundError):
            select_standard_remuneration(history, date(2025, 8, 1))


class GradeTableTest(unittest.TestCase):
    def test_lookup_boundaries(self):
        self.assertEqual(HEALTH_TABLE.lookup(0).standard, D("58000"))
        self.assertEqual(HEALTH_TABLE.lookup(62999).standard, D("58000"))
        self.assertEqual(HEALTH_TABLE.lookup(63000).standard, D("68000"))
        self.assertEqual(HEALTH_TABLE.lookup(115000).standard, D("118000"))
        self.assertEqual(HEALTH_TABLE.lookup(5000000).standard, D("1390000"))

    def test_health_and_pension_separately(self):
        """同じ報酬月額でも健保と厚年は別々に等級を決める（2.1）。"""
        self.assertEqual(HEALTH_TABLE.lookup(60000).grade, 1)
        self.assertEqual(PENSION_TABLE.lookup(60000).standard, D("88000"))
        self.assertEqual(HEALTH_TABLE.lookup(800000).standard, D("790000"))
        self.assertEqual(PENSION_TABLE.lookup(800000).standard, D("650000"))
        self.assertEqual(PENSION_TABLE.lookup(800000).grade, 32)

    def test_by_standard(self):
        self.assertEqual(PENSION_TABLE.by_standard(98000).grade, 2)
        with self.assertRaises(LookupError):
            PENSION_TABLE.by_standard(58000)

    def test_special_thresholds_derived(self):
        """SIR-45：上下限の特例の境界値（2.6 の例示 1,415,000円・665,000円）。"""
        self.assertEqual(HEALTH_TABLE.upper_special_threshold, D("1415000"))
        self.assertEqual(PENSION_TABLE.upper_special_threshold, D("665000"))
        self.assertEqual(HEALTH_TABLE.lower_special_threshold, D("53000"))
        self.assertEqual(PENSION_TABLE.lower_special_threshold, D("83000"))

    def test_raised_pension_cap_by_new_version(self):
        """SIR-03：厚年の上限引上げ（68万円）は新しい版の登録だけで反映できる。"""
        grades = list(PENSION_TABLE.grades[:-1]) + [
            Grade(32, D("650000"), D("635000"), D("665000")),
            Grade(33, D("680000"), D("665000"), None),
        ]
        table = GradeTable("pension", date(2027, 9, 1), grades)
        self.assertEqual(table.lookup(700000).standard, D("680000"))
        self.assertEqual(table.top.grade, 33)
        self.assertIs(select_version([PENSION_TABLE, table], date(2027, 9, 1)), table)
        self.assertIs(select_version([PENSION_TABLE, table], date(2027, 8, 1)), PENSION_TABLE)

    def test_invalid_table(self):
        with self.assertRaises(ValueError):
            GradeTable("health", date(2020, 1, 1), [Grade(1, 1, 0, 10), Grade(2, 2, 11, None)])
        with self.assertRaises(ValueError):
            GradeTable("other", date(2020, 1, 1), list(HEALTH_TABLE.grades))


if __name__ == "__main__":
    unittest.main()
