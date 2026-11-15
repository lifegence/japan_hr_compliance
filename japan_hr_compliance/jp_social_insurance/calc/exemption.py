"""産前産後休業・育児休業等の保険料免除（SIR-50〜52、2.7、6.6）。

免除は健保・介護・支援金・厚年の本人・事業主負担と拠出金に適用し、雇用保険料には
適用しない（SIR-52）。月額は monthly.calculate_monthly_premium の exempt、
賞与は bonus.calculate_bonus_premium の exempt に結果を渡す。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from ._util import add_months, iter_months, month_end, month_start, next_day, previous_day

MATERNITY = "maternity"
"""産前産後休業（14日ルールなし）"""
CHILDCARE = "childcare"
"""育児休業等"""
POSTNATAL_CHILDCARE = "postnatal_childcare"
"""出生時育児休業（育児休業等に含む。就業日は14日の計算から除く）"""

CHILDCARE_KINDS = frozenset({CHILDCARE, POSTNATAL_CHILDCARE})

REASON_MONTH_END = "month_end"
REASON_FOURTEEN_DAYS = "fourteen_days"


@dataclass(frozen=True)
class LeavePeriod:
    """休業1件（休暇の申請または確定した勤怠）。start・end は両端を含む。

    work_days: 出生時育児休業中の就業日（14日の計算から除く）
    """

    start: date
    end: date
    kind: str
    work_days: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("休業の終了日が開始日より前です")
        if self.kind not in (MATERNITY,) and self.kind not in CHILDCARE_KINDS:
            raise ValueError(f"休業の種類が不正です: {self.kind}")


@dataclass(frozen=True)
class LeaveBlock:
    """日が連続する休業をまとめた1回の休業。"""

    start: date
    end: date
    periods: tuple[LeavePeriod, ...] = field(default=())

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(p.kind for p in self.periods)

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def kind_on(self, d: date) -> str:
        for p in self.periods:
            if p.start <= d <= p.end:
                return p.kind
        raise LookupError(d)

    def leave_days(self) -> int:
        """暦日数から出生時育児休業中の就業日を除いた休業日数。"""
        work = {w for p in self.periods for w in p.work_days if p.start <= w <= p.end}
        return (self.end - self.start).days + 1 - len(work)


def merge_continuous(periods: Iterable[LeavePeriod]) -> list[LeaveBlock]:
    """日が連続する（または重なる）休業を1回の休業にまとめる。"""
    ordered = sorted(periods, key=lambda p: (p.start, p.end))
    blocks: list[LeaveBlock] = []
    for p in ordered:
        if blocks and p.start <= next_day(blocks[-1].end):
            last = blocks[-1]
            blocks[-1] = LeaveBlock(last.start, max(last.end, p.end), last.periods + (p,))
        else:
            blocks.append(LeaveBlock(p.start, p.end, (p,)))
    return blocks


def monthly_exemption_months(periods: Sequence[LeavePeriod]) -> dict[date, str]:
    """月額保険料の免除月と理由を返す（SIR-50）。

    ① 月末に休業中の月（産休・育休を通算して判定。休業開始月から終了日の翌日が属する
       月の前月まで）
    ② 育児休業等で、開始日の属する月と終了日の翌日が属する月が同一の場合に、その月の
       休業日数（暦日。同月内の複数回は通算、出生時育児休業の就業日を除く）が14日以上の月。
       産休には適用しない。月をまたぐ休業の翌月には適用しない
    """
    result: dict[date, str] = {}
    for block in merge_continuous(periods):
        for m in iter_months(block.start, block.end):
            if block.contains(month_end(m)):
                result[m] = REASON_MONTH_END

    days_by_month: dict[date, int] = {}
    for block in merge_continuous(p for p in periods if p.kind in CHILDCARE_KINDS):
        m = month_start(block.start)
        if month_start(next_day(block.end)) == m:
            days_by_month[m] = days_by_month.get(m, 0) + block.leave_days()
    for m, days in days_by_month.items():
        if days >= 14 and m not in result:
            result[m] = REASON_FOURTEEN_DAYS
    return dict(sorted(result.items()))


def one_month_expiry(start: date) -> date:
    """start から起算した1か月の満了日（応当日の前日。応当日がなければその月の末日）。"""
    target = add_months(start, 1)
    if target.day != start.day:  # 応当日がない
        return month_end(target)
    return previous_day(target)


def is_bonus_exempt(pay_date: date, periods: Sequence[LeavePeriod]) -> bool:
    """賞与の保険料が免除されるか（SIR-51）。

    産休：支給月の末日が産休中（支給月が免除月）であること
    育休：支給月の末日を含む、日が連続した育児休業等が1か月を超えること
      （ちょうど1か月は免除しない）
    """
    last_day = month_end(pay_date)
    for block in merge_continuous(periods):
        if block.contains(last_day) and block.kind_on(last_day) == MATERNITY:
            return True
    for block in merge_continuous(p for p in periods if p.kind in CHILDCARE_KINDS):
        if block.contains(last_day) and block.end > one_month_expiry(block.start):
            return True
    return False
