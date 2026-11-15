"""基準日・付与テーブル・区分・付与予定のテスト（規則 5.1・5.3、7章 #1〜4・8〜11・20・25・26）。"""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from japan_hr_compliance.jp_leave.calc.attendance import DayRecord, DayStatus, calc_attendance_rate
from japan_hr_compliance.jp_leave.calc.category import (
    AnnualDaysBasis,
    ContractPeriod,
    ContractTerms,
    MissingContractData,
    category_from_annual_days,
    default_annual_days_from_actual,
    determine_category,
    pick_contract_at,
    select_grant_table,
)
from japan_hr_compliance.jp_leave.calc.dates import (
    add_months,
    base_date_ordinal,
    first_base_date,
    months_covering,
    nth_base_date,
    service_months,
)
from japan_hr_compliance.jp_leave.calc.five_day import is_obligation_target
from japan_hr_compliance.jp_leave.calc.grant import PlanStatus, plan_individual_grant
from japan_hr_compliance.jp_leave.calc.grant_table import (
    LEGAL_GRANT_TABLE,
    LEGAL_SERVICE_MONTHS,
    GrantTable,
    GrantTableRow,
    WorkCategory,
    build_legal_table,
    find_below_statutory,
    legal_days,
)

D = date
FULL_TIME = ContractTerms(weekly_hours=Decimal(40), weekly_days=5)


def full_attendance(start: date, end: date, status: DayStatus = DayStatus.WORKED):
    """期間の全日を所定労働日とし、全日を ``status`` とした算定明細。"""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return calc_attendance_rate(start, end, days, {d: DayRecord(status) for d in days})


def attendance_with_absences(start: date, end: date, absent: int):
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    records = {d: DayRecord(DayStatus.ABSENT if i < absent else DayStatus.WORKED) for i, d in enumerate(days)}
    return calc_attendance_rate(start, end, days, records)


class TestBaseDate(unittest.TestCase):
    """LVR-05、規則 5.1、7章 #9。"""

    def test_table_5_1(self):
        cases = [
            (D(2026, 4, 1), [D(2026, 10, 1), D(2027, 10, 1), D(2028, 10, 1)]),
            (D(2026, 4, 15), [D(2026, 10, 15), D(2027, 10, 15), D(2028, 10, 15)]),
            (D(2026, 8, 28), [D(2027, 2, 28), D(2028, 2, 28), D(2029, 2, 28)]),
            (D(2026, 8, 31), [D(2027, 2, 28), D(2028, 2, 28), D(2029, 2, 28)]),
        ]
        for hire, expected in cases:
            with self.subTest(hire=hire):
                self.assertEqual([nth_base_date(hire, n) for n in (1, 2, 3)], expected)

    def test_leap_day_first_base_date(self):
        hire = D(2027, 8, 29)
        self.assertEqual(first_base_date(hire), D(2028, 2, 29))
        self.assertEqual(nth_base_date(hire, 2), D(2029, 2, 28))
        self.assertEqual(nth_base_date(hire, 5), D(2032, 2, 29))
        self.assertEqual(base_date_ordinal(hire, D(2029, 2, 28)), 2)

    def test_not_a_base_date(self):
        with self.assertRaises(ValueError):
            base_date_ordinal(D(2026, 4, 1), D(2027, 9, 30))

    def test_service_months(self):
        self.assertEqual(service_months(D(2026, 4, 1), D(2026, 10, 1)), 6)
        self.assertEqual(service_months(D(2026, 8, 31), D(2027, 2, 28)), 6)
        self.assertEqual(service_months(D(2027, 8, 29), D(2029, 2, 28)), 18)
        self.assertEqual(service_months(D(2026, 4, 1), D(2026, 9, 30)), 5)

    def test_month_helpers(self):
        self.assertEqual(add_months(D(2026, 1, 31), 1), D(2026, 2, 28))
        self.assertEqual(add_months(D(2026, 10, 1), -2), D(2026, 8, 1))
        self.assertEqual(months_covering(D(2026, 10, 1), D(2028, 3, 31)), 18)
        self.assertEqual(months_covering(D(2026, 10, 15), D(2027, 10, 1)), 12)  # 端数は切上げ


class TestGrantTable(unittest.TestCase):
    """LVR-02・03、7章 #1・2・11。"""

    def test_normal_days_by_service(self):  # 7章 #1
        self.assertEqual(
            [legal_days(m, WorkCategory.NORMAL) for m in LEGAL_SERVICE_MONTHS],
            [Decimal(x) for x in (10, 11, 12, 14, 16, 18, 20)],
        )
        self.assertEqual(legal_days(200, WorkCategory.NORMAL), Decimal(20))  # 6.5年以上
        self.assertEqual(legal_days(5, WorkCategory.NORMAL), Decimal(0))
        self.assertEqual(legal_days(17, WorkCategory.NORMAL), Decimal(10))

    def test_ratio_28_patterns(self):  # 7章 #2
        expected = {
            4: (7, 8, 9, 10, 12, 13, 15),
            3: (5, 6, 6, 8, 9, 10, 11),
            2: (3, 4, 4, 5, 6, 6, 7),
            1: (1, 2, 2, 2, 3, 3, 3),
        }
        count = 0
        for weekly_days, row in expected.items():
            terms = ContractTerms(weekly_hours=Decimal(20), weekly_days=weekly_days)
            cat = determine_category(terms).category
            for months, days in zip(LEGAL_SERVICE_MONTHS, row):
                with self.subTest(weekly_days=weekly_days, months=months):
                    self.assertEqual(LEGAL_GRANT_TABLE.days_for(months, cat), Decimal(days))
                    count += 1
        self.assertEqual(count, 28)

    def test_legal_table_has_no_warning(self):
        self.assertEqual(find_below_statutory(build_legal_table()), [])

    def test_below_statutory_warning(self):  # 7章 #11、LVR-03 受入基準
        table = build_legal_table("正社員用")
        table.rows = [r for r in table.rows if not (r.category == WorkCategory.NORMAL and r.service_months == 18)]
        table.rows.append(GrantTableRow(18, WorkCategory.NORMAL, Decimal(10)))
        warnings = find_below_statutory(table)
        self.assertEqual(len(warnings), 1)
        w = warnings[0]
        self.assertEqual((w.category, w.service_months, w.table_days, w.legal_days), (WorkCategory.NORMAL, 18, 10, 11))
        self.assertIn("法定の 11 日を下回っています", w.message)

    def test_company_table_above_statutory_and_missing_rows(self):
        rows = [GrantTableRow(0, WorkCategory.NORMAL, 10), GrantTableRow(12, WorkCategory.NORMAL, 20)]
        table = GrantTable("上乗せ", rows)
        warnings = find_below_statutory(table)
        # 通常は全段階で法定以上、比例付与の区分は行がないので全段階で警告
        self.assertFalse([w for w in warnings if w.category == WorkCategory.NORMAL])
        self.assertEqual(len(warnings), 4 * 7)
        self.assertEqual(table.days_for(3, WorkCategory.NORMAL), Decimal(10))

    def test_select_table_by_work_rule(self):  # LVR-04
        part = GrantTable("パート用", build_legal_table().rows, work_rule="パート")
        tables = {"パート": part}
        self.assertIs(select_grant_table(ContractTerms(Decimal(20), 3, work_rule="パート"), tables), part)
        self.assertIs(select_grant_table(ContractTerms(Decimal(40), 5, work_rule="正社員"), tables), LEGAL_GRANT_TABLE)


class TestCategory(unittest.TestCase):
    """LVR-07・08、7章 #3・4・25。"""

    def test_weekly_hours_boundary(self):  # 7章 #3
        self.assertEqual(determine_category(ContractTerms(Decimal("29.5"), 4)).category, WorkCategory.WEEK4)
        d = determine_category(ContractTerms(Decimal(30), 4))
        self.assertEqual(d.category, WorkCategory.NORMAL)
        self.assertFalse(d.is_ratio)

    def test_annual_days_boundaries(self):  # 7章 #4
        expected = {
            48: WorkCategory.WEEK1, 72: WorkCategory.WEEK1, 73: WorkCategory.WEEK2, 120: WorkCategory.WEEK2,
            121: WorkCategory.WEEK3, 168: WorkCategory.WEEK3, 169: WorkCategory.WEEK4, 216: WorkCategory.WEEK4,
            217: WorkCategory.NORMAL,
        }
        for days, cat in expected.items():
            with self.subTest(days=days):
                terms = ContractTerms(Decimal(25), weekly_days=None, annual_days=days)
                self.assertEqual(determine_category(terms).category, cat)
        self.assertEqual(category_from_annual_days(47), WorkCategory.NONE)

    def test_shift_worker_from_actual(self):  # 7章 #25
        annual = default_annual_days_from_actual(60, is_initial=True)
        self.assertEqual(annual, 120)
        d = determine_category(ContractTerms(Decimal(20)), actual_annual_days=annual)
        self.assertEqual(d.category, WorkCategory.WEEK2)
        self.assertEqual(d.annual_days_basis, AnnualDaysBasis.ACTUAL)
        self.assertEqual(default_annual_days_from_actual(150, is_initial=False), 150)

    def test_estimate_vs_actual_uses_larger(self):  # LVR-08
        est_low = ContractTerms(Decimal(20), annual_days=150, annual_days_basis=AnnualDaysBasis.ESTIMATE)
        d = determine_category(est_low, actual_annual_days=180)
        self.assertEqual((d.category, d.annual_days_basis), (WorkCategory.WEEK4, AnnualDaysBasis.ACTUAL))
        est_high = ContractTerms(Decimal(20), annual_days=180, annual_days_basis=AnnualDaysBasis.ESTIMATE)
        d = determine_category(est_high, actual_annual_days=150)
        self.assertEqual((d.category, d.annual_days_basis), (WorkCategory.WEEK4, AnnualDaysBasis.ESTIMATE))

    def test_missing_data(self):
        with self.assertRaises(MissingContractData):
            determine_category(ContractTerms(None, 3))
        with self.assertRaises(MissingContractData):
            determine_category(ContractTerms(Decimal(20)))


class TestGrantPlan(unittest.TestCase):
    """LVR-14・20、規則 5.3、7章 #1・8・10・20・26。"""

    hire = D(2026, 4, 1)

    def plan(self, base, contract=FULL_TIME, attendance=None, **kw):
        if attendance is None:
            prev = None if base_date_ordinal(self.hire, base) == 1 else add_months(base, -12)
            attendance = full_attendance(prev or self.hire, base - timedelta(days=1))
        return plan_individual_grant(self.hire, base, contract, attendance, **kw)

    def test_normal_grants_each_base_date(self):  # 7章 #1
        days = [self.plan(nth_base_date(self.hire, n)).days for n in range(1, 9)]
        self.assertEqual(days, [Decimal(x) for x in (10, 11, 12, 14, 16, 18, 20, 20)])

    def test_below_80_then_next_year(self):  # 7章 #8、LVR-14
        first = self.plan(D(2026, 10, 1), attendance=attendance_with_absences(D(2026, 4, 1), D(2026, 9, 30), 50))
        self.assertEqual(first.status, PlanStatus.NO_GRANT)
        self.assertEqual(first.days, 0)
        self.assertIn("8割未満", first.reasons[0])
        second = self.plan(D(2027, 10, 1))
        self.assertEqual((second.status, second.service_months, second.days), (PlanStatus.PLANNED, 18, Decimal(11)))

    def test_example_5_3_contract_changes(self):  # 規則 5.3、7章 #10
        history = [
            ContractPeriod(D(2026, 4, 1), ContractTerms(Decimal(24), 3)),
            ContractPeriod(D(2027, 4, 1), ContractTerms(Decimal(28), 4)),
            ContractPeriod(D(2028, 9, 30), ContractTerms(Decimal(32), 4)),  # 基準日の前日に変更
        ]
        expected = [
            (D(2026, 10, 1), WorkCategory.WEEK3, 5),
            (D(2027, 10, 1), WorkCategory.WEEK4, 8),
            (D(2028, 10, 1), WorkCategory.NORMAL, 12),
        ]
        for base, cat, days in expected:
            with self.subTest(base=base):
                p = self.plan(base, contract=pick_contract_at(history, base))
                self.assertEqual((p.category, p.days), (cat, Decimal(days)))

    def test_contract_change_day_before_base_date(self):  # 7章 #10
        history = [
            ContractPeriod(D(2026, 4, 1), ContractTerms(Decimal(24), 3)),
            ContractPeriod(D(2026, 9, 30), ContractTerms(Decimal(28), 4)),
        ]
        p = self.plan(D(2026, 10, 1), contract=pick_contract_at(history, D(2026, 10, 1)))
        self.assertEqual((p.category, p.days), (WorkCategory.WEEK4, Decimal(7)))

    def test_ratio_five_day_target(self):  # 7章 #20、LVR-50
        week4 = ContractTerms(Decimal(28), 4)
        p10 = self.plan(nth_base_date(self.hire, 4), contract=week4)  # 3.5年
        self.assertEqual(p10.days, Decimal(10))
        self.assertTrue(p10.is_five_day_target)
        p9 = self.plan(nth_base_date(self.hire, 3), contract=week4)  # 2.5年
        self.assertEqual(p9.days, Decimal(9))
        self.assertFalse(p9.is_five_day_target)
        self.assertTrue(is_obligation_target(10))
        self.assertFalse(is_obligation_target(Decimal("9.5")))

    def test_pending_when_attendance_unrecorded(self):  # 7章 #26、LVR-16
        start, end = D(2026, 4, 1), D(2026, 9, 30)
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        records = {d: DayRecord(DayStatus.WORKED) for d in days[:-3]}
        p = self.plan(D(2026, 10, 1), attendance=calc_attendance_rate(start, end, days, records))
        self.assertEqual(p.status, PlanStatus.PENDING)
        self.assertIn("未確定", p.reasons[0])

    def test_pending_when_contract_missing_or_service_unconfirmed(self):
        self.assertEqual(self.plan(D(2026, 10, 1), contract=None).status, PlanStatus.PENDING)
        p = self.plan(D(2026, 10, 1), service_start_confirmed=False)
        self.assertEqual(p.status, PlanStatus.PENDING)

    def test_service_start_before_hire(self):  # LVR-06（定年後再雇用の通算）
        p = self.plan(D(2026, 10, 1), service_start=D(2016, 4, 1))
        self.assertEqual((p.service_months, p.days), (126, Decimal(20)))


if __name__ == "__main__":
    unittest.main()
