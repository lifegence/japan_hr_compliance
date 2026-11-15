"""年5日の取得義務。LVR-50〜53、規則 2.6・5.6。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from typing import Iterable, List, Optional, Sequence, Tuple

from .dates import add_months, add_years, months_covering
from .grant_table import Number, to_days


REQUIRED_DAYS = Decimal(5)
TARGET_GRANT_DAYS = Decimal(10)


class TakeKind(str, Enum):
    """取得の区分（LVR-41）。いずれも年5日の取得日数に数える。"""

    REQUEST = "本人の請求"
    PLANNED = "計画的付与"
    DESIGNATED = "時季指定"


class TakeUnit(str, Enum):
    FULL = "1日"
    HALF = "半日"
    HOURLY = "時間単位"  # P2。年5日には数えない


class OverlapMethod(str, Enum):
    """履行期間が重なる場合の方式（LVR-51）。"""

    PRORATE = "按分"
    EACH = "各期間で5日"


class NoticeStage(str, Enum):
    """通知の段階（LVR-53）。"""

    EARLY = "早期注意"
    ACTION = "要対応"
    URGENT = "緊急"


NOTICE_MESSAGES = {
    NoticeStage.EARLY: "年5日の履行期間の50%が経過しました。年次有給休暇の取得状況を確認してください。",
    NoticeStage.ACTION: "履行期間の残りが3か月です。未取得の日数がある場合は、時季指定を検討してください。",
    NoticeStage.URGENT: "履行期間の終了まで2か月です。年5日を取得できないおそれがあります。速やかに時季指定を行ってください。",
}


@dataclass(frozen=True)
class LeaveTake:
    """年休等の取得1件（1日・半日・時間単位）。

    ``is_annual_leave`` が False のもの（会社独自の特別休暇など）は年5日に数えない。
    """

    day: date
    unit: TakeUnit = TakeUnit.FULL
    kind: TakeKind = TakeKind.REQUEST
    is_annual_leave: bool = True
    hours: Optional[Decimal] = None

    @property
    def counted_days(self) -> Decimal:
        """年5日の取得日数に数える日数（LVR-52）。"""
        if not self.is_annual_leave or self.unit == TakeUnit.HOURLY:
            return Decimal(0)
        return Decimal("0.5") if self.unit == TakeUnit.HALF else Decimal(1)


def is_obligation_target(grant_days: Number) -> bool:
    """年5日の取得義務の対象か。その回の付与日数（繰越分を除く）が10日以上（LVR-50）。"""
    return to_days(grant_days) >= TARGET_GRANT_DAYS


@dataclass(frozen=True)
class ObligationSource:
    """履行期間の起点。

    Attributes:
        start: 基準日（前倒しの場合は付与日、分割の場合は10日到達日）。
        count_from: 取得日数を数え始める日。分割付与で10日到達日より前の取得を含める場合に
            最初の分割付与の日を入れる。省略時は ``start``。
        label: 管理簿等に表示する名前（付与の名称など）。
    """

    start: date
    count_from: Optional[date] = None
    label: Optional[str] = None


@dataclass(frozen=True)
class ObligationPeriod:
    """年5日の履行期間（JP Five Day Obligation に対応）。"""

    start: date
    end: date
    required_days: Decimal
    count_from: date
    sources: Tuple[ObligationSource, ...]
    months: int = 12

    @property
    def is_prorated(self) -> bool:
        return len(self.sources) > 1


def single_period_end(start: date) -> date:
    """起点から1年の期間の末日。"""
    return add_years(start, 1) - timedelta(days=1)


def prorated_required_days(months: int, half_day_allowed: bool = True) -> Decimal:
    """按分の必要日数＝月数÷12×5日。端数は0.5日単位（半日取得なしの会社は1日単位）に切り上げる。"""
    raw = Fraction(months * 5, 12)
    unit = Fraction(1, 2) if half_day_allowed else Fraction(1)
    units = math.ceil(raw / unit)
    value = units * unit
    return Decimal(value.numerator) / Decimal(value.denominator)


def build_obligation_periods(
    sources: Sequence[ObligationSource],
    method: OverlapMethod = OverlapMethod.PRORATE,
    half_day_allowed: bool = True,
) -> List[ObligationPeriod]:
    """履行期間と必要日数を決める（LVR-51）。

    各起点から1年を履行期間とする。期間が重なる場合、按分方式では最初の期間の初日から
    後の期間の末日までを1つの期間にまとめ、必要日数を「月数÷12×5日」とする
    （月数の1か月未満の端数は切り上げ）。「各期間で5日」方式では重なりをそのままにする。
    """
    ordered = sorted(sources, key=lambda s: s.start)
    if method == OverlapMethod.EACH:
        return [
            ObligationPeriod(s.start, single_period_end(s.start), REQUIRED_DAYS, s.count_from or s.start, (s,))
            for s in ordered
        ]

    periods: List[ObligationPeriod] = []
    group: List[ObligationSource] = []
    group_end: Optional[date] = None
    for s in ordered + [None]:  # type: ignore[list-item]
        if s is not None and group_end is not None and s.start <= group_end:
            group.append(s)
            group_end = max(group_end, single_period_end(s.start))
            continue
        if group:
            start = group[0].start
            count_from = min(g.count_from or g.start for g in group)
            if len(group) == 1:
                periods.append(ObligationPeriod(start, group_end, REQUIRED_DAYS, count_from, tuple(group)))
            else:
                months = months_covering(start, group_end)
                periods.append(
                    ObligationPeriod(
                        start, group_end, prorated_required_days(months, half_day_allowed), count_from,
                        tuple(group), months,
                    )
                )
        if s is not None:
            group = [s]
            group_end = single_period_end(s.start)
    return periods


def count_taken_days(takes: Iterable[LeaveTake], period: ObligationPeriod) -> Decimal:
    """履行期間に数える取得日数（LVR-52）。``count_from``〜期間末日の取得を数える。"""
    return sum(
        (t.counted_days for t in takes if period.count_from <= t.day <= period.end),
        Decimal(0),
    )


@dataclass(frozen=True)
class NoticePoint:
    stage: NoticeStage
    day: date
    message: str


def notice_points(period: ObligationPeriod) -> List[NoticePoint]:
    """通知時点（LVR-53）。期間開始から6か月・9か月、期間終了の2か月前。

    「期間終了の2か月前」は履行期間の末日の2か月前の応答日とする
    （2026-10-01〜2027-09-30 なら 2027-07-30）。
    """
    return [
        NoticePoint(NoticeStage.EARLY, add_months(period.start, 6), NOTICE_MESSAGES[NoticeStage.EARLY]),
        NoticePoint(NoticeStage.ACTION, add_months(period.start, 9), NOTICE_MESSAGES[NoticeStage.ACTION]),
        NoticePoint(NoticeStage.URGENT, add_months(period.end, -2), NOTICE_MESSAGES[NoticeStage.URGENT]),
    ]


@dataclass(frozen=True)
class ObligationStatus:
    """``as_of`` 時点の履行状況（未達見込みの一覧の1行）。"""

    period: ObligationPeriod
    as_of: date
    taken_days: Decimal
    shortfall: Decimal
    elapsed_ratio: Fraction
    days_left: int
    stage: Optional[NoticeStage]

    @property
    def is_met(self) -> bool:
        return self.shortfall <= 0


def obligation_status(period: ObligationPeriod, takes: Iterable[LeaveTake], as_of: date) -> ObligationStatus:
    """履行状況（取得日数・未取得日数・経過率・残り日数・到達済みの段階）を返す。

    ``as_of`` までの取得だけを数える。
    """
    taken = sum(
        (t.counted_days for t in takes if period.count_from <= t.day <= min(period.end, as_of)),
        Decimal(0),
    )
    total = (period.end - period.start).days + 1
    elapsed = min(max((as_of - period.start).days, 0), total)
    stage = None
    for p in notice_points(period):
        if as_of >= p.day:
            stage = p.stage
    return ObligationStatus(
        period=period,
        as_of=as_of,
        taken_days=taken,
        shortfall=max(period.required_days - taken, Decimal(0)),
        elapsed_ratio=Fraction(elapsed, total),
        days_left=max((period.end - as_of).days + 1, 0) if as_of >= period.start else total,
        stage=stage,
    )


def due_notices(period: ObligationPeriod, takes: Iterable[LeaveTake], as_of: date) -> List[NoticePoint]:
    """``as_of`` が通知時点に当たり、未取得の日数が残っていれば、その通知を返す。"""
    takes = list(takes)
    status = obligation_status(period, takes, as_of)
    if status.is_met:
        return []
    return [p for p in notice_points(period) if p.day == as_of]
