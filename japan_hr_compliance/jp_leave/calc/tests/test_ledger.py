"""2年時効と充当のテスト（規則 5.4、LVR-30〜34、7章 #12〜15・24）。"""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from japan_hr_compliance.jp_leave.calc.ledger import (
    GrantState,
    InsufficientBalance,
    LeaveLedger,
    TakeDay,
    expiry_date,
)

D = date


def days_from(start: date, n: int):
    return [TakeDay(start + timedelta(days=i)) for i in range(n)]


class TestExpiry(unittest.TestCase):
    """LVR-30。"""

    def test_two_years(self):
        self.assertEqual(expiry_date(D(2026, 10, 1)), D(2028, 9, 30))
        self.assertEqual(expiry_date(D(2027, 10, 1)), D(2029, 9, 30))

    def test_leap_day_grant(self):  # 応答日がない場合はその月の末日まで使用可
        self.assertEqual(expiry_date(D(2028, 2, 29)), D(2030, 2, 28))
        self.assertEqual(expiry_date(D(2028, 2, 29), 4), D(2032, 2, 28))  # 応答日 2032-02-29 がある
        self.assertEqual(expiry_date(D(2027, 3, 1)), D(2029, 2, 28))
        led = LeaveLedger()
        led.add_grant("G1", D(2028, 2, 29), 10)
        self.assertEqual(led.consume("A", [TakeDay(D(2030, 2, 28))])[0].grant_id, "G1")
        self.assertEqual([e.effective_date for e in led.process_lapse(D(2030, 3, 1))], [D(2030, 3, 1)])

    def test_longer_validity_allowed_shorter_rejected(self):
        self.assertEqual(expiry_date(D(2026, 10, 1), 3), D(2029, 9, 30))
        with self.assertRaises(ValueError):
            expiry_date(D(2026, 10, 1), 1)
        with self.assertRaises(ValueError):
            LeaveLedger(validity_years=1)

    def test_lapse_on_next_day(self):  # LVR-30 受入基準
        led = LeaveLedger()
        led.add_grant("G1", D(2026, 10, 1), 10)
        self.assertEqual(led.process_lapse(D(2028, 9, 30)), [])
        entries = led.process_lapse(D(2028, 10, 1))
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0].effective_date, entries[0].days), (D(2028, 10, 1), Decimal(10)))
        self.assertEqual(led.grants["G1"].state, GrantState.EXPIRED)


class TestExample54(unittest.TestCase):
    """規則 5.4 の表を順に再現する（7章 #12）。"""

    def test_scenario(self):
        led = LeaveLedger()

        def snapshot():
            return [led.remaining(g) for g in ("G1", "G2", "G3") if g in led.grants]

        led.add_grant("G1", D(2026, 10, 1), 10)
        self.assertEqual((snapshot(), led.balance()), ([10], 10))

        led.consume("A1", [TakeDay(D(2027, 6, 10), 3)])
        self.assertEqual((snapshot(), led.balance()), ([7], 7))

        led.add_grant("G2", D(2027, 10, 1), 11)
        self.assertEqual((snapshot(), led.balance()), ([7, 11], 18))

        rows = led.consume("A2", [TakeDay(D(2027, 12, 20), 5)])
        self.assertEqual({r.grant_id for r in rows}, {"G1"})
        self.assertEqual((snapshot(), led.balance()), ([2, 11], 13))

        rows = led.consume("A3", [TakeDay(D(2028, 9, 29), 3)])
        self.assertEqual([(r.grant_id, r.amount) for r in rows], [("G1", 2), ("G2", 1)])  # 2行に分ける
        self.assertEqual((snapshot(), led.balance()), ([0, 10], 10))

        led.add_grant("G3", D(2028, 10, 1), 12)
        self.assertEqual(led.process_lapse(D(2028, 10, 1)), [])  # ①は残0で失効（台帳行なし）
        self.assertEqual(led.grants["G1"].state, GrantState.EXPIRED)
        self.assertEqual((snapshot(), led.balance()), ([0, 10, 12], 22))

        led.add_grant("G4", D(2029, 10, 1), 14)
        entries = led.process_lapse(D(2029, 10, 1))
        self.assertEqual([(e.grant_id, e.days) for e in entries], [("G2", 10)])
        self.assertEqual(led.balance(), 26)

    def test_multi_day_consecutive_takes_split_rows(self):
        led = LeaveLedger()
        led.add_grant("G1", D(2026, 10, 1), 2)
        led.add_grant("G2", D(2027, 10, 1), 11)
        rows = led.consume("A", days_from(D(2027, 11, 1), 3))
        self.assertEqual([r.grant_id for r in rows], ["G1", "G1", "G2"])


class TestExpiryBoundary(unittest.TestCase):
    """7章 #13：失効日の前日・当日・翌日の取得。"""

    def setUp(self):
        self.led = LeaveLedger()
        self.led.add_grant("G1", D(2026, 10, 1), 10)  # 失効日 2028-09-30
        self.led.add_grant("G2", D(2027, 10, 1), 11)

    def test_day_before(self):
        rows = self.led.consume("A", [TakeDay(D(2028, 9, 29))])
        self.assertEqual(rows[0].grant_id, "G1")

    def test_expiry_day(self):
        rows = self.led.consume("A", [TakeDay(D(2028, 9, 30))])
        self.assertEqual(rows[0].grant_id, "G1")

    def test_day_after(self):
        rows = self.led.consume("A", [TakeDay(D(2028, 10, 1))])
        self.assertEqual(rows[0].grant_id, "G2")
        self.assertEqual(self.led.balance(D(2028, 10, 1)), 10)


class TestCancel(unittest.TestCase):
    """LVR-33、7章 #14。"""

    def test_cancel_restores(self):
        led = LeaveLedger()
        led.add_grant("G1", D(2026, 10, 1), 10)
        led.consume("A", [TakeDay(D(2027, 1, 5)), TakeDay(D(2027, 1, 6), "0.5", "午前")])
        self.assertEqual(led.balance(), Decimal("8.5"))
        res = led.cancel("A", D(2027, 1, 4))
        self.assertFalse(res.needs_notification)
        self.assertEqual(led.balance(), 10)
        self.assertEqual(led.cancel("A", D(2027, 1, 4)).restored, [])  # 二重取消は何もしない

    def test_cancel_after_expiry_not_restored(self):
        led = LeaveLedger()
        led.add_grant("G1", D(2026, 10, 1), 10)
        led.add_grant("G2", D(2027, 10, 1), 11)
        led.consume("A", [TakeDay(D(2028, 9, 29)), TakeDay(D(2028, 9, 30)), TakeDay(D(2028, 10, 2))])
        self.assertEqual((led.remaining("G1"), led.remaining("G2")), (8, 10))
        led.process_lapse(D(2028, 10, 1))
        res = led.cancel("A", D(2028, 10, 5))
        self.assertTrue(res.needs_notification)
        self.assertEqual([(c.grant_id, c.day) for c in res.not_restored], [("G1", D(2028, 9, 29)), ("G1", D(2028, 9, 30))])
        self.assertEqual([c.grant_id for c in res.restored], ["G2"])
        self.assertEqual((led.remaining("G1"), led.remaining("G2")), (0, 11))

    def test_cancel_after_expiry_before_lapse_job(self):
        led = LeaveLedger()
        led.add_grant("G1", D(2026, 10, 1), 10)
        led.consume("A", [TakeDay(D(2028, 9, 30))])
        res = led.cancel("A", D(2028, 10, 1))  # 日次の失効処理の前でも失効日を過ぎていれば戻さない
        self.assertEqual(len(res.not_restored), 1)
        self.assertEqual(led.remaining("G1"), 9)


class TestCrossBaseDate(unittest.TestCase):
    """7章 #15：基準日をまたぐ連続5日の申請。"""

    def test_before_grant_days_use_old_grant(self):
        led = LeaveLedger()
        led.add_grant("G1", D(2026, 10, 1), 2)
        led.add_grant("G2", D(2027, 10, 1), 11)
        rows = led.consume("A", days_from(D(2027, 9, 29), 5))
        self.assertEqual(
            [(r.day, r.grant_id) for r in rows],
            [(D(2027, 9, 29), "G1"), (D(2027, 9, 30), "G1"), (D(2027, 10, 1), "G2"), (D(2027, 10, 2), "G2"), (D(2027, 10, 3), "G2")],
        )
        self.assertEqual(len({r.application_id for r in rows}), 1)

    def test_new_grant_not_used_before_grant_date(self):
        led = LeaveLedger()
        led.add_grant("G1", D(2026, 10, 1), 1)
        led.add_grant("G2", D(2027, 10, 1), 11)
        with self.assertRaises(InsufficientBalance):
            led.consume("A", days_from(D(2027, 9, 29), 5))
        self.assertEqual(led.balance(), 12)  # 失敗時は何も変わらない
        self.assertEqual(led.consumptions, [])


class TestRetirementAndValidation(unittest.TestCase):
    def test_retirement(self):  # LVR-34、7章 #24
        led = LeaveLedger()
        led.add_grant("G1", D(2026, 10, 1), 10)
        led.add_grant("G2", D(2027, 10, 1), 11)
        led.consume("A", [TakeDay(D(2027, 11, 1), 4)])
        entries = led.extinguish_on_retirement(D(2028, 3, 31))
        self.assertEqual([(e.grant_id, e.days, e.reason) for e in entries], [("G1", 6, GrantState.EXTINGUISHED), ("G2", 11, GrantState.EXTINGUISHED)])
        self.assertEqual(led.balance(), 0)

    def test_take_amount_validation(self):
        with self.assertRaises(ValueError):
            TakeDay(D(2027, 1, 1), "0.3")
        with self.assertRaises(ValueError):
            TakeDay(D(2027, 1, 1), 0)
        self.assertEqual(TakeDay(D(2027, 1, 1), 0.5).amount, Decimal("0.5"))

    def test_duplicate_grant(self):
        led = LeaveLedger()
        led.add_grant("G1", D(2026, 10, 1), 10)
        with self.assertRaises(ValueError):
            led.add_grant("G1", D(2026, 10, 1), 10)


if __name__ == "__main__":
    unittest.main()
