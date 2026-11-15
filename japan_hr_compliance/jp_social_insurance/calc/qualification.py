"""資格の取得・喪失と保険料の対象月、控除月（SIR-11、SIR-24〜26、2.3、6.3・6.4）。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date

from ._util import add_months, month_start, next_day

NEXT_MONTH = "next_month"
"""翌月控除（前月分を当月の給与から控除。既定）"""

CURRENT_MONTH = "current_month"
"""当月控除"""


def loss_date_for_retirement(retirement_date: date) -> date:
    """退職日から資格喪失日（翌日）を求める。"""
    return next_day(retirement_date)


@dataclass(frozen=True)
class Qualification:
    """1つの保険（健保・厚年など）の資格期間。lost は喪失日（退職日の翌日）。"""

    acquired: date
    lost: date | None = None

    def __post_init__(self) -> None:
        if self.lost is not None and self.lost <= self.acquired:
            raise ValueError("喪失日は取得日より後でなければなりません")

    @property
    def is_same_month_gain_loss(self) -> bool:
        """同月得喪（取得月に喪失）か。"""
        return self.lost is not None and month_start(self.lost) == month_start(self.acquired)

    @property
    def last_premium_month(self) -> date | None:
        """保険料の最終月。喪失していなければ None。"""
        if self.lost is None:
            return None
        if self.is_same_month_gain_loss:
            return month_start(self.acquired)
        return add_months(month_start(self.lost), -1)

    def is_premium_month(self, target_month: date) -> bool:
        """対象月に保険料がかかるか。

        取得月（日割りなし）から喪失月の前月まで。同月得喪の月は1か月分かかる。
        """
        month = month_start(target_month)
        if month < month_start(self.acquired):
            return False
        last = self.last_premium_month
        return last is None or month <= last

    def is_loss_month(self, target_month: date) -> bool:
        """対象月が喪失月（保険料がかからない月）か。同月得喪の月は含まない。"""
        return (
            self.lost is not None
            and not self.is_same_month_gain_loss
            and month_start(target_month) == month_start(self.lost)
        )

    def with_loss_no_later_than(self, lost: date | None) -> Qualification:
        """年齢到達などの喪失日を反映した資格期間を返す（早い方の喪失日）。"""
        if lost is None or (self.lost is not None and self.lost <= lost):
            return self
        if lost <= self.acquired:
            raise ValueError("取得日以前に喪失しています")
        return replace(self, lost=lost)


@dataclass(frozen=True)
class DeductionPlan:
    """1回の給与で控除する保険料の対象月。

    target_months: この給与で控除する対象月（古い順）
    deferred_months: 退職時の2か月控除を取り消したため、この給与で控除しない月。
        徴収方法（別途振込／翌月以降の支給から控除）を呼び出し側で記録する（SIR-26）
    """

    target_months: tuple[date, ...]
    deferred_months: tuple[date, ...] = ()


def plan_deduction(
    pay_month: date,
    timing: str,
    qualifications: Iterable[Qualification],
    *,
    is_final_pay: bool = False,
    deduct_two_months: bool = True,
) -> DeductionPlan:
    """支給月の給与で控除する保険料の対象月を決める（SIR-24〜26）。

    timing: NEXT_MONTH（前月分を控除）か CURRENT_MONTH（当月分を控除）
    qualifications: 健保・厚年など。いずれかの保険料の対象月であれば対象月に含める
    is_final_pay: 退職者の最終給与か
    deduct_two_months: 翌月控除の最終給与で、当月分もあわせて控除するか（既定 True）

    翌月控除の最終給与では、支給月がまだ保険料の対象月（月末退職・同月得喪）であれば
    当月分も控除する。月末前日退職では支給月が喪失月になるため2か月控除にならない。
    """
    quals = tuple(qualifications)
    if not quals:
        raise ValueError("資格期間を1件以上渡してください")
    month = month_start(pay_month)

    def covered(m: date) -> bool:
        return any(q.is_premium_month(m) for q in quals)

    if timing == CURRENT_MONTH:
        return DeductionPlan(tuple(m for m in (month,) if covered(m)))
    if timing != NEXT_MONTH:
        raise ValueError(f"控除月の設定が不正です: {timing}")

    targets = [m for m in (add_months(month, -1),) if covered(m)]
    deferred: list[date] = []
    if is_final_pay and covered(month):
        (targets if deduct_two_months else deferred).append(month)
    return DeductionPlan(tuple(targets), tuple(deferred))
