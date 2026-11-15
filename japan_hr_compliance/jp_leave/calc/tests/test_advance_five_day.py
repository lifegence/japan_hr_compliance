"""前倒し・分割付与と年5日の取得義務のテスト（規則 5.5・5.6、7章 #16〜21・28）。"""

import unittest
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from japan_hr_compliance.jp_leave.calc.advance import (
    MANUAL_CHECK_ITEMS,
    GrantInput,
    GrantKind,
    judge_advance_grants,
    legal_attendance_period,
)
from japan_hr_compliance.jp_leave.calc.five_day import (
    LeaveTake,
    NoticeStage,
    ObligationSource,
    OverlapMethod,
    TakeKind,
    TakeUnit,
    build_obligation_periods,
    count_taken_days,
    due_notices,
    notice_points,
    obligation_status,
    prorated_required_days,
)

D = date
HIRE_55 = D(2026, 10, 1)
GRANTS_55 = [GrantInput(D(2026, 10, 1), 10), GrantInput(D(2027, 4, 1), 11), GrantInput(D(2028, 4, 1), 12)]


class TestAdvanceExample55(unittest.TestCase):
    """規則 5.5、LVR-22・23、7章 #16。"""

    def test_reference_values_and_ok(self):
        j = judge_advance_grants(HIRE_55, GRANTS_55)
        self.assertTrue(j.ok, j.errors)
        self.assertEqual([r.legal_base_date for r in j.rounds], [D(2027, 4, 1), D(2028, 4, 1), D(2029, 4, 1)])
        self.assertEqual([r.legal_days for r in j.rounds], [10, 11, 12])
        self.assertEqual([r.kind for r in j.rounds], [GrantKind.ADVANCED] * 3)
        self.assertEqual((j.rounds[0].advance.months, j.rounds[0].advance.days), (6, 0))
        self.assertEqual(j.rounds[1].advance.months, 12)
        self.assertEqual([r.ten_day_reach_date for r in j.rounds], [D(2026, 10, 1), D(2027, 4, 1), D(2028, 4, 1)])
        self.assertEqual(j.manual_check_items, MANUAL_CHECK_ITEMS)
        self.assertTrue(j.manual_check_items)

    def test_second_advance_shorter_is_error(self):  # 7章 #16 後段
        grants = [GrantInput(D(2026, 10, 1), 10), GrantInput(D(2028, 1, 1), 11)]
        j = judge_advance_grants(HIRE_55, grants)
        self.assertFalse(j.ok)
        self.assertIn("初年度の繰上げ期間（6か月）より短く", j.rounds[1].errors[0])
        self.assertEqual(j.rounds[1].advance.months, 3)

    def test_same_advance_is_ok(self):
        grants = [GrantInput(D(2026, 10, 1), 10), GrantInput(D(2027, 10, 1), 11)]
        self.assertTrue(judge_advance_grants(HIRE_55, grants).ok)

    def test_legal_days_not_reached(self):
        grants = [GrantInput(D(2026, 10, 1), 8)]
        j = judge_advance_grants(HIRE_55, grants)
        self.assertFalse(j.ok)
        self.assertIn("法定の 10 日に達していません", j.errors[0])

    def test_grant_after_legal_base_is_error(self):
        j = judge_advance_grants(D(2026, 4, 1), [GrantInput(D(2026, 10, 2), 10)])
        self.assertFalse(j.ok)

    def test_statutory_and_above(self):
        hire = D(2026, 4, 1)
        j = judge_advance_grants(hire, [GrantInput(D(2026, 10, 1), 10), GrantInput(D(2027, 10, 1), 15)])
        self.assertTrue(j.ok)
        self.assertEqual([r.kind for r in j.rounds], [GrantKind.STATUTORY, GrantKind.ABOVE_STATUTORY])

    def test_legal_attendance_period(self):
        self.assertEqual(legal_attendance_period(HIRE_55, 1), (D(2026, 10, 1), D(2027, 3, 31)))
        self.assertEqual(legal_attendance_period(HIRE_55, 2), (D(2027, 4, 1), D(2028, 3, 31)))


class TestSplitGrant(unittest.TestCase):
    """7章 #17、LVR-23。入社時5日・6か月後5日。"""

    def test_split(self):
        hire = D(2026, 4, 1)
        grants = [GrantInput(D(2026, 4, 1), 5, 1), GrantInput(D(2026, 10, 1), 5, 1)]
        j = judge_advance_grants(hire, grants)
        self.assertTrue(j.ok, j.errors)
        r = j.rounds[0]
        self.assertEqual(r.kind, GrantKind.SPLIT)
        self.assertEqual(r.cumulative_after_each, [5, 10])
        self.assertEqual(r.ten_day_reach_date, D(2026, 10, 1))
        self.assertTrue(r.is_five_day_target)

        # 1回目の時点では年5日の期間を作らない
        first_only = judge_advance_grants(hire, grants[:1])
        self.assertIsNone(first_only.rounds[0].ten_day_reach_date)
        self.assertFalse(first_only.rounds[0].is_five_day_target)

        # 10日到達日を起点、最初の分割付与の日から取得を数える
        period = build_obligation_periods([ObligationSource(r.ten_day_reach_date, r.grants[0].grant_date)])[0]
        self.assertEqual((period.start, period.end, period.required_days), (D(2026, 10, 1), D(2027, 9, 30), 5))
        takes = [LeaveTake(D(2026, 7, 1)), LeaveTake(D(2027, 2, 1))]
        self.assertEqual(count_taken_days(takes, period), 2)

    def test_split_remainder_after_legal_base(self):
        hire = D(2026, 4, 1)
        grants = [GrantInput(D(2026, 4, 1), 5, 1), GrantInput(D(2026, 11, 1), 5, 1)]
        j = judge_advance_grants(hire, grants)
        self.assertFalse(j.ok)
        self.assertEqual(j.rounds[0].cumulative_by_legal_base, 5)


class TestFiveDayObligation(unittest.TestCase):
    """規則 5.6、LVR-51〜53、7章 #18・19・21・28。"""

    sources = [ObligationSource(D(2026, 10, 1)), ObligationSource(D(2027, 4, 1)), ObligationSource(D(2028, 4, 1))]

    def test_double_track_prorate(self):  # 7章 #18
        periods = build_obligation_periods(self.sources)
        self.assertEqual(len(periods), 2)
        p = periods[0]
        self.assertEqual((p.start, p.end, p.months, p.required_days), (D(2026, 10, 1), D(2028, 3, 31), 18, Decimal("7.5")))
        self.assertTrue(p.is_prorated)
        q = periods[1]
        self.assertEqual((q.start, q.end, q.required_days), (D(2028, 4, 1), D(2029, 3, 31), 5))

    def test_double_track_each(self):  # 7章 #18
        periods = build_obligation_periods(self.sources[:2], OverlapMethod.EACH)
        self.assertEqual(
            [(p.start, p.end, p.required_days) for p in periods],
            [(D(2026, 10, 1), D(2027, 9, 30), 5), (D(2027, 4, 1), D(2028, 3, 31), 5)],
        )

    def test_no_half_day_company(self):  # 7章 #28
        periods = build_obligation_periods(self.sources[:2], half_day_allowed=False)
        self.assertEqual(periods[0].required_days, 8)

    def test_rounding(self):
        self.assertEqual(prorated_required_days(18), Decimal("7.5"))
        self.assertEqual(prorated_required_days(13), Decimal("5.5"))  # 5.416… → 5.5
        self.assertEqual(prorated_required_days(13, half_day_allowed=False), 6)
        self.assertEqual(prorated_required_days(12), 5)

    def test_individual_base_dates_5_4(self):  # 規則 5.6 後段（5.4 の従業員）
        periods = build_obligation_periods([ObligationSource(D(2026, 10, 1)), ObligationSource(D(2027, 10, 1))])
        self.assertEqual([(p.start, p.end, p.required_days) for p in periods],
                         [(D(2026, 10, 1), D(2027, 9, 30), 5), (D(2027, 10, 1), D(2028, 9, 30), 5)])
        takes = [LeaveTake(D(2027, 6, 10) + timedelta(days=i)) for i in range(3)]
        takes += [LeaveTake(D(2027, 12, 20) + timedelta(days=i)) for i in range(5)]
        takes += [LeaveTake(D(2028, 9, 27) + timedelta(days=i)) for i in range(3)]
        first = obligation_status(periods[0], takes, D(2027, 9, 30))
        self.assertEqual((first.taken_days, first.shortfall, first.is_met), (3, 2, False))
        second = obligation_status(periods[1], takes, D(2028, 9, 30))
        self.assertEqual((second.taken_days, second.is_met), (8, True))

    def test_counted_days(self):  # 7章 #19、LVR-52
        p = build_obligation_periods([ObligationSource(D(2026, 10, 1))])[0]
        takes = [
            LeaveTake(D(2026, 11, 1), TakeUnit.HALF),
            LeaveTake(D(2026, 11, 2), TakeUnit.HOURLY, hours=Decimal(2)),
            LeaveTake(D(2026, 11, 3), TakeUnit.FULL, TakeKind.PLANNED),
            LeaveTake(D(2026, 11, 4), TakeUnit.FULL, TakeKind.DESIGNATED),
            LeaveTake(D(2026, 11, 5), is_annual_leave=False),  # 特別休暇
            LeaveTake(D(2027, 10, 1)),  # 期間外
        ]
        self.assertEqual(count_taken_days(takes, p), Decimal("2.5"))

    def test_notice_points(self):  # 7章 #21、LVR-53
        p = build_obligation_periods([ObligationSource(D(2026, 10, 1))])[0]
        pts = notice_points(p)
        self.assertEqual([(x.stage, x.day) for x in pts], [
            (NoticeStage.EARLY, D(2027, 4, 1)),
            (NoticeStage.ACTION, D(2027, 7, 1)),
            (NoticeStage.URGENT, D(2027, 7, 30)),  # 末日 2027-09-30 の2か月前の応答日
        ])
        takes = [LeaveTake(D(2026, 12, 1))]
        self.assertEqual([n.stage for n in due_notices(p, takes, D(2027, 7, 1))], [NoticeStage.ACTION])
        self.assertEqual(due_notices(p, takes, D(2027, 7, 2)), [])
        self.assertEqual([n.stage for n in due_notices(p, takes, D(2027, 7, 30))], [NoticeStage.URGENT])
        done = [LeaveTake(D(2026, 12, 1) + timedelta(days=i)) for i in range(5)]
        self.assertEqual(due_notices(p, done, D(2027, 7, 1)), [])

        st = obligation_status(p, takes, D(2027, 4, 1))
        self.assertEqual((st.stage, st.shortfall), (NoticeStage.EARLY, 4))
        self.assertEqual(st.elapsed_ratio, Fraction(182, 365))

    def test_notice_points_prorated(self):
        p = build_obligation_periods(self.sources[:2])[0]
        self.assertEqual([x.day for x in notice_points(p)], [D(2027, 4, 1), D(2027, 7, 1), D(2028, 1, 31)])


if __name__ == "__main__":
    unittest.main()
