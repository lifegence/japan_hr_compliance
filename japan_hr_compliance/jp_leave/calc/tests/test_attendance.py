"""出勤率の算定のテスト（規則 5.2、LVR-10〜15、7章 #5〜7・27）。"""

import unittest
from datetime import date, timedelta
from fractions import Fraction

from japan_hr_compliance.jp_leave.calc.attendance import (
    SHORTENED_KEY,
    SHORTENED_NOTE,
    DayRecord,
    DayStatus,
    LeaveTreatment,
    attendance_period,
    calc_attendance_rate,
    check_treatment_change,
    meets_eighty_percent,
)

D = date
START, END = D(2026, 4, 1), D(2026, 9, 30)


def period_days(start=START, end=END):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def build_case(absent: int, scheduled_count: int = 122, employer_caused: int = 3, annual_leave: int = 2, holiday_work: int = 2):
    """5.2 の条件：所定労働日 122、経営上の障害による休業 3、年休 2、所定休日出勤 2。"""
    all_days = period_days()
    scheduled = all_days[:scheduled_count]
    holidays = all_days[scheduled_count:]
    statuses = (
        [DayStatus.EMPLOYER_CAUSED] * employer_caused
        + [DayStatus.ABSENT] * absent
        + [DayStatus.ANNUAL_LEAVE] * annual_leave
    )
    statuses += [DayStatus.WORKED] * (scheduled_count - len(statuses))
    records = {d: DayRecord(s) for d, s in zip(scheduled, statuses)}
    for d in holidays[:holiday_work]:
        records[d] = DayRecord(DayStatus.WORKED)
    return scheduled, records


class TestEightyPercent(unittest.TestCase):
    """LVR-14、7章 #5。"""

    def test_boundaries(self):
        self.assertTrue(meets_eighty_percent(80, 100))
        self.assertFalse(meets_eighty_percent(79, 100))
        self.assertFalse(meets_eighty_percent(95, 119))  # 79.8%
        self.assertTrue(meets_eighty_percent(96, 119))  # 80.7%


class TestExample52(unittest.TestCase):
    """規則 5.2 の計算例、7章 #5・7、LVR-11 受入基準。"""

    def test_case_a(self):
        scheduled, records = build_case(absent=24)
        r = calc_attendance_rate(START, END, scheduled, records)
        self.assertEqual(r.scheduled_days, 122)
        self.assertEqual(r.excluded, {DayStatus.EMPLOYER_CAUSED.value: 3})
        self.assertEqual(r.holiday_work_days, 2)
        self.assertEqual(r.total_working_days, 119)
        self.assertEqual(r.absent_days, 24)
        self.assertEqual(r.deemed, {DayStatus.ANNUAL_LEAVE.value: 2})
        self.assertEqual(r.attended_days, 95)
        self.assertEqual(r.attended_days, r.total_working_days - r.absent_days)
        self.assertFalse(r.eligible)  # 950 < 952

    def test_case_b(self):
        scheduled, records = build_case(absent=23)
        r = calc_attendance_rate(START, END, scheduled, records)
        self.assertEqual((r.total_working_days, r.attended_days), (119, 96))
        self.assertTrue(r.eligible)  # 960 >= 952
        self.assertEqual(r.rate, Fraction(96, 119))

    def test_period(self):  # LVR-10
        self.assertEqual(attendance_period(D(2026, 4, 1), D(2026, 10, 1)), (START, END))
        self.assertEqual(
            attendance_period(D(2026, 4, 1), D(2027, 10, 1), D(2026, 10, 1)), (D(2026, 10, 1), D(2027, 9, 30))
        )


class TestDeemedAndExcluded(unittest.TestCase):
    """LVR-11・12・13、7章 #6・27。"""

    def test_full_period_statutory_leave(self):  # 7章 #6
        days = period_days()
        for status in (DayStatus.MATERNITY, DayStatus.CHILDCARE, DayStatus.OCCUPATIONAL_INJURY, DayStatus.FAMILY_CARE):
            with self.subTest(status=status):
                r = calc_attendance_rate(START, END, days, {d: DayRecord(status) for d in days})
                self.assertEqual(r.rate, 1)
                self.assertTrue(r.eligible)
                self.assertEqual(r.worked_days, 0)

    def test_four_exclusions_and_not_attributable(self):
        days = period_days()[:100]
        statuses = [DayStatus.FORCE_MAJEURE, DayStatus.EMPLOYER_CAUSED, DayStatus.STRIKE, DayStatus.NOT_ATTRIBUTABLE]
        records = {d: DayRecord(statuses[i] if i < 4 else DayStatus.WORKED) for i, d in enumerate(days)}
        r = calc_attendance_rate(START, END, days, records)
        self.assertEqual(r.total_working_days, 97)
        self.assertEqual(r.attended_days, 97)
        self.assertEqual(r.deemed, {DayStatus.NOT_ATTRIBUTABLE.value: 1})
        self.assertEqual(sum(r.excluded.values()), 3)

    def test_other_leave_treatment(self):  # LVR-13
        days = period_days()[:100]
        records = {d: DayRecord(DayStatus.WORKED) for d in days}
        for d in days[:21]:
            records[d] = DayRecord(DayStatus.OTHER_LEAVE, "子の看護等休暇")

        default = calc_attendance_rate(START, END, days, records)
        self.assertEqual((default.total_working_days, default.attended_days), (100, 100))
        self.assertEqual(default.treatments, {"子の看護等休暇": "出勤とみなす"})

        absent = calc_attendance_rate(START, END, days, records, {"子の看護等休暇": LeaveTreatment.ABSENT})
        self.assertEqual((absent.total_working_days, absent.attended_days), (100, 79))
        self.assertFalse(absent.eligible)
        self.assertEqual(absent.treatments, {"子の看護等休暇": "欠勤"})

        excluded = calc_attendance_rate(START, END, days, records, {"子の看護等休暇": LeaveTreatment.EXCLUDED})
        self.assertEqual((excluded.total_working_days, excluded.attended_days), (79, 79))

    def test_treatment_change_warning(self):  # 7章 #27
        w = check_treatment_change("子の看護等休暇", LeaveTreatment.ABSENT)
        self.assertIsNotNone(w)
        self.assertIn("不利益取扱い", w.message)
        self.assertIsNone(check_treatment_change("子の看護等休暇", LeaveTreatment.DEEMED))

    def test_unrecorded_is_pending(self):  # LVR-16
        days = period_days()[:10]
        records = {d: DayRecord(DayStatus.WORKED) for d in days[:8]}
        records[days[8]] = DayRecord(DayStatus.UNRECORDED)
        r = calc_attendance_rate(START, END, days, records)
        self.assertTrue(r.is_pending)
        self.assertEqual(r.unrecorded_days, (days[8], days[9]))
        self.assertIsNone(r.eligible)

    def test_all_excluded_is_undetermined(self):
        days = period_days()[:5]
        r = calc_attendance_rate(START, END, days, {d: DayRecord(DayStatus.FORCE_MAJEURE) for d in days})
        self.assertEqual(r.total_working_days, 0)
        self.assertIsNone(r.rate)
        self.assertIsNone(r.eligible)


class TestShortenedPeriod(unittest.TestCase):
    """規則 2.4 ①・5.5、LVR-22 ①：短縮された期間の所定労働日は出勤とみなし分子・分母に加える。"""

    def test_second_uniform_grant(self):
        # 5.5：2027-04-01 付与の実際の算定期間は 2026-10-01〜2027-03-31、
        # 法定の算定期間は 2027-04-01〜2028-03-31（全体が短縮された期間）。
        start, end = D(2026, 10, 1), D(2027, 3, 31)
        actual = period_days(start, end)[:100]
        legal_scheduled = period_days(D(2027, 4, 1), D(2028, 3, 31))[:240]
        records = {d: DayRecord(DayStatus.ABSENT if i < 30 else DayStatus.WORKED) for i, d in enumerate(actual)}
        r = calc_attendance_rate(start, end, actual + legal_scheduled, records, legal_period=(D(2027, 4, 1), D(2028, 3, 31)))
        self.assertEqual(r.scheduled_days, 100)
        self.assertEqual(r.shortened_days, 366)
        self.assertEqual(r.shortened_deemed_days, 240)
        self.assertEqual(r.deemed, {SHORTENED_KEY: 240})
        self.assertEqual((r.total_working_days, r.attended_days), (340, 310))
        self.assertTrue(r.eligible)  # 実在期間だけなら 70/100 で付与なし
        self.assertIn(SHORTENED_NOTE, r.notes)
        self.assertFalse(calc_attendance_rate(start, end, actual, records).eligible)

    def test_partial_overlap(self):
        # 法定の算定期間 2026-10-01〜2027-03-31 のうち、実際の算定期間 2027-01-01〜2027-03-31 に
        # 含まれない 2026-10-01〜2026-12-31（92日）が短縮された期間。
        legal = (D(2026, 10, 1), D(2027, 3, 31))
        start, end = D(2027, 1, 1), D(2027, 3, 31)
        scheduled = period_days(*legal)
        records = {d: DayRecord(DayStatus.WORKED) for d in period_days(start, end)}
        r = calc_attendance_rate(start, end, scheduled, records, legal_period=legal)
        self.assertEqual((r.shortened_days, r.shortened_deemed_days), (92, 92))
        self.assertEqual(r.scheduled_days, 90)
        self.assertEqual((r.total_working_days, r.attended_days), (182, 182))

    def test_no_shortening(self):
        days = period_days()
        r = calc_attendance_rate(START, END, days, {d: DayRecord(DayStatus.WORKED) for d in days}, legal_period=(START, END))
        self.assertEqual((r.shortened_days, r.shortened_deemed_days, r.notes), (0, 0, ()))

    def test_grant_at_hire_has_empty_period(self):
        hire = D(2026, 10, 1)
        start, end = attendance_period(hire, hire)
        legal = (hire, D(2027, 3, 31))
        r = calc_attendance_rate(start, end, period_days(*legal)[:120], {}, legal_period=legal)
        self.assertEqual((r.shortened_days, r.shortened_deemed_days), (182, 120))
        self.assertEqual(r.rate, 1)
        self.assertTrue(r.eligible)
        # 所定労働日が渡されなくても、期間が空なら全期間出勤とみなす
        self.assertTrue(calc_attendance_rate(start, end, [], {}, legal_period=legal).eligible)


if __name__ == "__main__":
    unittest.main()
