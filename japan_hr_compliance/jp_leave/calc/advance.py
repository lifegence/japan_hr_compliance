"""斉一的付与・前倒し付与・分割付与の判定。LVR-21〜23、規則 2.4・5.5。

付与の「回」（round）ごとに、対応する法定の基準日・法定の日数と比べる。
分割付与は同じ回の付与を複数件に分けたもの（``round_no`` を同じにする）。

判定する事項（LVR-22）
    ② 次年度以降の付与日の繰上げ期間が初年度の繰上げ期間より短くないこと。
    ③ 各回の付与日数の累計が、法定の基準日までに法定日数に達していること。
① の出勤率の特例は :func:`calc_attendance_rate` の ``legal_period`` で扱う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .dates import add_months, full_months_between, nth_base_date, service_months
from .grant_table import LEGAL_GRANT_TABLE, GrantTable, Number, WorkCategory, to_days


FIVE_DAY_THRESHOLD = Decimal(10)

#: システムで判定できず、担当者の確認チェックが必要な事項（LVR-22）
MANUAL_CHECK_ITEMS: Tuple[str, ...] = (
    "就業規則に前倒し・斉一的付与（付与日・付与日数）の定めがあること",
    "短縮された期間を出勤率の算定で出勤とみなす取扱いを就業規則等で定めていること",
)


class GrantKind(str, Enum):
    """付与の種別（LVR-23）。"""

    STATUTORY = "法定どおり"
    ADVANCED = "前倒し"
    SPLIT = "分割"
    ABOVE_STATUTORY = "法定を上回る付与"
    ADJUSTMENT = "調整"


@dataclass(frozen=True)
class GrantInput:
    """担当者が入力した付与（付与日・付与日数）。

    ``round_no`` は第何回の付与か（1 始まり）。分割付与では同じ値にする。
    省略した場合は、前の付与の次の回とみなす。
    """

    grant_date: date
    days: Decimal
    round_no: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "days", to_days(self.days))


@dataclass(frozen=True)
class Advance:
    """繰上げ期間（法定の基準日−付与日）を「月数＋日数」で表す。"""

    months: int
    days: int

    @staticmethod
    def between(grant_date: date, legal_base_date: date) -> "Advance":
        m = full_months_between(grant_date, legal_base_date)
        d = (legal_base_date - add_months(grant_date, m)).days
        return Advance(m, d)

    def satisfied_by(self, grant_date: date, legal_base_date: date) -> bool:
        """``grant_date`` が ``legal_base_date`` から自分以上に繰り上がっているか。"""
        return add_months(grant_date, self.months) + timedelta(days=self.days) <= legal_base_date

    def __str__(self) -> str:
        return f"{self.months}か月{self.days}日" if self.days else f"{self.months}か月"


@dataclass
class RoundJudgement:
    """1回分の付与の判定結果（年休付与に記録する項目）。"""

    round_no: int
    legal_base_date: date
    legal_days: Decimal
    grants: List[GrantInput]
    first_grant_date: date
    advance: Advance
    cumulative_by_legal_base: Decimal
    total_days: Decimal
    cumulative_after_each: List[Decimal]
    ten_day_reach_date: Optional[date]
    kind: GrantKind
    errors: List[str] = field(default_factory=list)

    @property
    def is_five_day_target(self) -> bool:
        """年5日の取得義務の対象（その回の付与日数の累計が10日以上）。"""
        return self.ten_day_reach_date is not None


@dataclass
class AdvanceJudgement:
    """前倒し・分割付与の判定結果。``ok`` が False なら確定できない。"""

    rounds: List[RoundJudgement]
    manual_check_items: Tuple[str, ...] = MANUAL_CHECK_ITEMS

    @property
    def errors(self) -> List[str]:
        return [e for r in self.rounds for e in r.errors]

    @property
    def ok(self) -> bool:
        return not self.errors


def ten_day_reach_date(grants: Sequence[GrantInput]) -> Optional[date]:
    """付与日数の累計が10日に達した日（LVR-23）。達しなければ None。"""
    total = Decimal(0)
    for g in sorted(grants, key=lambda g: g.grant_date):
        total += g.days
        if total >= FIVE_DAY_THRESHOLD:
            return g.grant_date
    return None


def _assign_rounds(grants: Sequence[GrantInput]) -> Dict[int, List[GrantInput]]:
    rounds: Dict[int, List[GrantInput]] = {}
    current = 0
    for g in sorted(grants, key=lambda g: g.grant_date):
        if g.round_no is None:
            current += 1
            no = current
        else:
            if g.round_no < current:
                raise ValueError("付与の回は付与日の順に指定してください")
            no = current = g.round_no
        rounds.setdefault(no, []).append(g)
    expected = list(range(1, len(rounds) + 1))
    if sorted(rounds) != expected:
        raise ValueError("付与の回は 1 から連続して指定してください")
    return rounds


def judge_advance_grants(
    hire_date: date,
    grants: Sequence[GrantInput],
    category: WorkCategory = WorkCategory.NORMAL,
    service_start: Optional[date] = None,
    table: GrantTable = LEGAL_GRANT_TABLE,
    legal_days_override: Optional[Mapping[int, Number]] = None,
) -> AdvanceJudgement:
    """前倒し・分割付与の要件を判定する（LVR-22・23）。

    Args:
        hire_date: 雇入れ日（法定の基準日の起点）。
        grants: 入力された付与。
        category: 区分（法定の日数の参考表示に使う）。
        service_start: 勤続通算起算日（省略時は雇入れ日）。
        table: 法定の日数を引く表（既定は法定の表）。
        legal_days_override: 回ごとの法定の日数を直接与える場合（出勤率8割未満の回など）。

    Returns:
        回ごとの法定の基準日・法定の日数・繰上げ期間・累計・10日到達日・種別・エラー。
    """
    if not grants:
        return AdvanceJudgement([])
    start = service_start or hire_date
    overrides = {k: to_days(v) for k, v in (legal_days_override or {}).items()}
    by_round = _assign_rounds(grants)

    results: List[RoundJudgement] = []
    first_advance: Optional[Advance] = None
    for no in sorted(by_round):
        items = sorted(by_round[no], key=lambda g: g.grant_date)
        legal_base = nth_base_date(hire_date, no)
        legal = overrides.get(no, table.days_for(service_months(start, legal_base), category))
        first_date = items[0].grant_date
        errors: List[str] = []

        if first_date > legal_base:
            errors.append(f"第{no}回の付与日 {first_date} が法定の基準日 {legal_base} より後です")
            advance = Advance(0, 0)
        else:
            advance = Advance.between(first_date, legal_base)

        if no == 1:
            first_advance = advance
        elif first_advance is not None and first_date <= legal_base:
            if not first_advance.satisfied_by(first_date, legal_base):
                errors.append(
                    f"第{no}回の付与日 {first_date} の繰上げ期間（{advance}）が"
                    f"初年度の繰上げ期間（{first_advance}）より短くなっています"
                )

        cumulative = []
        running = Decimal(0)
        for g in items:
            running += g.days
            cumulative.append(running)
        by_legal = sum((g.days for g in items if g.grant_date <= legal_base), Decimal(0))
        if by_legal < legal:
            errors.append(
                f"第{no}回の付与日数の累計が法定の基準日 {legal_base} までに {by_legal} 日で、"
                f"法定の {legal} 日に達していません"
            )

        if len(items) > 1:
            kind = GrantKind.SPLIT
        elif first_date < legal_base:
            kind = GrantKind.ADVANCED
        elif running > legal:
            kind = GrantKind.ABOVE_STATUTORY
        else:
            kind = GrantKind.STATUTORY

        results.append(
            RoundJudgement(
                round_no=no,
                legal_base_date=legal_base,
                legal_days=legal,
                grants=items,
                first_grant_date=first_date,
                advance=advance,
                cumulative_by_legal_base=by_legal,
                total_days=running,
                cumulative_after_each=cumulative,
                ten_day_reach_date=ten_day_reach_date(items),
                kind=kind,
                errors=errors,
            )
        )
    return AdvanceJudgement(results)


def legal_attendance_period(hire_date: date, round_no: int) -> Tuple[date, date]:
    """第 ``round_no`` 回の法定の算定期間（短縮期間の算出に使う）。"""
    start = hire_date if round_no == 1 else nth_base_date(hire_date, round_no - 1)
    return start, nth_base_date(hire_date, round_no) - timedelta(days=1)
