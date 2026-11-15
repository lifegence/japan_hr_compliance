"""標準報酬月額の決定：定時決定・随時改定・年間平均の保険者算定・休業終了時改定
（SIR-41〜45、SIR-48、2.6、6.7）。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._util import ZERO, Number, add_months, floor_yen, month_start, next_day, to_decimal
from .grades import Grade, GradeTable
from .qualification import Qualification

#: 被保険者区分（支払基礎日数の規則）
GENERAL = "general"
"""一般の被保険者（17日以上）"""
PART_TIME_THREE_QUARTERS = "part_time_three_quarters"
"""一般の被保険者のうち4分の3以上のパートタイマー等（17日以上の月がなければ15日以上）"""
SHORT_TIME = "short_time"
"""短時間労働者（4分の3未満で適用される者。11日以上）"""

#: 給与形態（SIR-41）
PAY_MONTHLY = "monthly"
PAY_DAILY = "daily"
PAY_HOURLY = "hourly"

INCREASE = "increase"
DECREASE = "decrease"


def payment_base_days(
    pay_type: str,
    *,
    calendar_days: int | None = None,
    has_absence_deduction: bool = False,
    prescribed_days: int | None = None,
    absence_days: int = 0,
    attendance_days: int | None = None,
) -> int:
    """支払基礎日数を給与形態ごとに求める（SIR-41）。

    月給制：計算期間の暦日数。欠勤控除がある場合は所定労働日数 − 欠勤日数
      （所定労働日数は就業規則で定めた方式の値を呼び出し側で渡す。absence_days は
      控除月ではなく、その計算期間の欠勤日数）
    日給・時給制：出勤日数（有給休暇を含む）
    """
    if pay_type == PAY_MONTHLY:
        if has_absence_deduction:
            if prescribed_days is None:
                raise ValueError("欠勤控除がある月給制は所定労働日数が必要です")
            return max(0, prescribed_days - absence_days)
        if calendar_days is None:
            raise ValueError("月給制は計算期間の暦日数が必要です")
        return calendar_days
    if pay_type in (PAY_DAILY, PAY_HOURLY):
        if attendance_days is None:
            raise ValueError("日給・時給制は出勤日数が必要です")
        return attendance_days
    raise ValueError(f"給与形態が不正です: {pay_type}")


@dataclass(frozen=True)
class MonthlyRemuneration:
    """支払月ごとの報酬実績（SIR-40）。

    month: 支払日の属する月
    retroactive_pay: 遡及昇給の差額（平均から除く。PY-012）
    excluded_reason: 平均から除く理由（途中入社で1か月分に満たない月、休職で低額の月など）。
        None なら算入候補
    """

    month: date
    base_days: int
    cash: Decimal
    in_kind: Decimal = ZERO
    retroactive_pay: Decimal = ZERO
    excluded_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "month", month_start(self.month))
        for name in ("cash", "in_kind", "retroactive_pay"):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))

    @property
    def amount(self) -> Decimal:
        """報酬額（通貨＋現物）。"""
        return self.cash + self.in_kind

    @property
    def adjusted_amount(self) -> Decimal:
        """遡及差額を除いた報酬額。"""
        return self.amount - self.retroactive_pay


def select_months_by_base_days(
    records: Sequence[MonthlyRemuneration], category: str
) -> list[MonthlyRemuneration]:
    """支払基礎日数の規則で平均の対象月を選ぶ（SIR-42。6.7）。

    除外理由のある月は先に除いてから日数の規則を当てはめる。
    """
    candidates = [r for r in records if r.excluded_reason is None]
    if category == GENERAL:
        return [r for r in candidates if r.base_days >= 17]
    if category == PART_TIME_THREE_QUARTERS:
        over17 = [r for r in candidates if r.base_days >= 17]
        return over17 if over17 else [r for r in candidates if r.base_days >= 15]
    if category == SHORT_TIME:
        return [r for r in candidates if r.base_days >= 11]
    raise ValueError(f"被保険者区分が不正です: {category}")


def average_remuneration(records: Sequence[MonthlyRemuneration]) -> Decimal | None:
    """遡及差額を除いた報酬額の平均（1円未満切捨て）。対象月がなければ None。"""
    if not records:
        return None
    return floor_yen(sum((r.adjusted_amount for r in records), ZERO) / len(records))


def is_regular_determination_target(
    year: int,
    qualification: Qualification,
    scheduled_revision_months: Iterable[date] = (),
) -> bool:
    """定時決定の対象か（SIR-43）。

    7月1日現在の被保険者で、6月1日以降の資格取得者と、7〜9月に随時改定・
    育児休業等終了時改定が予定される者を除く。
    """
    july1 = date(year, 7, 1)
    if qualification.acquired > july1 or (qualification.lost is not None and qualification.lost <= july1):
        return False
    if qualification.acquired >= date(year, 6, 1):
        return False
    return not any(
        date(year, 7, 1) <= month_start(m) <= date(year, 9, 1) for m in scheduled_revision_months
    )


@dataclass(frozen=True)
class RegularDetermination:
    """定時決定の結果。uses_previous=True なら従前の標準報酬月額で決定する。"""

    year: int
    months_used: tuple[date, ...]
    average: Decimal | None
    health: Grade | None
    pension: Grade | None
    effective_from: date

    @property
    def uses_previous(self) -> bool:
        return self.average is None


def regular_determination(
    year: int,
    records: Sequence[MonthlyRemuneration],
    category: str,
    health_table: GradeTable,
    pension_table: GradeTable | None,
) -> RegularDetermination:
    """定時決定（4・5・6月に支払った報酬。SIR-42・43）。適用は9月分から。

    records は4〜6月の実績。支払月が4〜6月以外のものはエラー。
    """
    for r in records:
        if not (date(year, 4, 1) <= r.month <= date(year, 6, 1)):
            raise ValueError(f"定時決定の対象外の月です: {r.month.isoformat()}")
    used = select_months_by_base_days(records, category)
    avg = average_remuneration(used)
    return RegularDetermination(
        year=year,
        months_used=tuple(r.month for r in used),
        average=avg,
        health=health_table.lookup(avg) if avg is not None else None,
        pension=pension_table.lookup(avg) if (avg is not None and pension_table) else None,
        effective_from=date(year, 9, 1),
    )


@dataclass(frozen=True)
class TableJudgement:
    """随時改定の、1つの等級表（健保または厚年）での判定結果。"""

    applicable: bool
    current: Grade
    new: Grade
    grade_difference: int
    special_case: bool
    reason: str


def judge_grade_change(
    table: GradeTable,
    *,
    current_standard: Number,
    average: Number,
    direction: str,
    current_remuneration: Number | None = None,
) -> TableJudgement:
    """2等級以上の差・方向の一致・上下限の特例を1つの等級表で判定する（SIR-45）。

    direction: 固定的賃金の変動の方向（INCREASE / DECREASE）
    current_remuneration: 従前の報酬月額。最下位等級からの上昇・最上位等級からの下降の
        特例の判定に使う（なければその特例は判定しない）
    特例の境界値は等級表から導出する（GradeTable.upper/lower_special_threshold）。
    """
    if direction not in (INCREASE, DECREASE):
        raise ValueError(f"変動の方向が不正です: {direction}")
    avg = to_decimal(average)
    current = table.by_standard(current_standard)
    new = table.lookup(avg)
    diff = new.grade - current.grade
    if (direction == INCREASE and diff <= 0) or (direction == DECREASE and diff >= 0):
        return TableJudgement(False, current, new, diff, False, "方向が一致しないか等級の差がない")
    if abs(diff) >= 2:
        return TableJudgement(True, current, new, diff, False, "2等級以上の差")

    top, bottom = table.top, table.bottom
    hi, lo = table.upper_special_threshold, table.lower_special_threshold
    prev = to_decimal(current_remuneration) if current_remuneration is not None else None
    special = False
    if direction == INCREASE:
        # 最上位の1つ下の等級から、境界値以上で最上位へ
        if current.grade == top.grade - 1 and avg >= hi:
            special = True
        # 最下位等級で従前の報酬月額が境界値未満の者が、1つ上の等級へ
        elif current.grade == bottom.grade and prev is not None and prev < lo:
            special = True
    else:
        # 最上位等級で従前の報酬月額が境界値以上の者が、1つ下の等級へ
        if current.grade == top.grade and prev is not None and prev >= hi:
            special = True
        # 最下位の1つ上の等級から、境界値未満で最下位へ
        elif current.grade == bottom.grade + 1 and avg < lo:
            special = True
    if special:
        return TableJudgement(True, current, new, diff, True, "上下限の特例（1等級差）")
    return TableJudgement(False, current, new, diff, False, "1等級差")


@dataclass(frozen=True)
class OccasionalRevision:
    """随時改定の判定結果（健保・厚年それぞれ。SIR-44・45）。"""

    change_month: date
    base_days_ok: bool
    average: Decimal
    health: TableJudgement | None
    pension: TableJudgement | None
    effective_from: date

    @property
    def applicable(self) -> bool:
        return self.base_days_ok and any(j is not None and j.applicable for j in (self.health, self.pension))


def occasional_revision(
    change_month: date,
    records: Sequence[MonthlyRemuneration],
    *,
    category: str,
    direction: str,
    health_table: GradeTable,
    pension_table: GradeTable | None,
    current_health_standard: Number,
    current_pension_standard: Number | None,
    current_remuneration: Number | None = None,
) -> OccasionalRevision:
    """随時改定を判定する（固定的賃金の変動がある候補者について。SIR-45）。

    records: 変動月から継続した3か月の実績（支払月）。支払基礎日数がいずれも17日
      （短時間労働者は11日）以上であることが要件。改定は変動月から4か月目の月分から。
    固定的賃金の変動のない者（非固定的賃金だけの増減）は候補にしない（呼び出し側。SIR-44）。
    """
    start = month_start(change_month)
    expected = [add_months(start, i) for i in range(3)]
    if sorted(r.month for r in records) != expected:
        raise ValueError("変動月から継続した3か月の実績を渡してください")
    threshold = 11 if category == SHORT_TIME else 17
    days_ok = all(r.base_days >= threshold for r in records)
    avg = floor_yen(sum((r.adjusted_amount for r in records), ZERO) / 3)
    health = judge_grade_change(
        health_table,
        current_standard=current_health_standard,
        average=avg,
        direction=direction,
        current_remuneration=current_remuneration,
    )
    pension = None
    if pension_table is not None and current_pension_standard is not None:
        pension = judge_grade_change(
            pension_table,
            current_standard=current_pension_standard,
            average=avg,
            direction=direction,
            current_remuneration=current_remuneration,
        )
    return OccasionalRevision(start, days_ok, avg, health, pension, add_months(start, 3))


@dataclass(frozen=True)
class AnnualAverageAssessment:
    """年間平均による保険者算定（申立て）の候補判定（SIR-48）。

    candidate: 季節的変動の申告があり、定時決定との差が健保・厚年のいずれかで2等級以上
    申立ての要否と本人の同意は担当者が確定する。
    """

    year: int
    months_used: tuple[date, ...]
    annual_average: Decimal | None
    health: Grade | None
    pension: Grade | None
    health_difference: int | None
    pension_difference: int | None
    candidate: bool


def annual_average_assessment(
    year: int,
    records: Sequence[MonthlyRemuneration],
    regular: RegularDetermination,
    *,
    category: str,
    health_table: GradeTable,
    pension_table: GradeTable | None,
    seasonal_business_reason: bool,
) -> AnnualAverageAssessment:
    """前年7月〜当年6月の実績から年間平均を求め、定時決定との差を判定する（SIR-48）。

    支払基礎日数の規則は定時決定と同じものを当てはめる。
    """
    first, last = date(year - 1, 7, 1), date(year, 6, 1)
    for r in records:
        if not (first <= r.month <= last):
            raise ValueError(f"年間平均の対象外の月です: {r.month.isoformat()}")
    used = select_months_by_base_days(records, category)
    avg = average_remuneration(used)
    health = health_table.lookup(avg) if avg is not None else None
    pension = pension_table.lookup(avg) if (avg is not None and pension_table) else None
    h_diff = (
        health.grade - regular.health.grade if (health is not None and regular.health is not None) else None
    )
    p_diff = (
        pension.grade - regular.pension.grade if (pension is not None and regular.pension is not None) else None
    )
    candidate = seasonal_business_reason and any(d is not None and abs(d) >= 2 for d in (h_diff, p_diff))
    return AnnualAverageAssessment(
        year, tuple(r.month for r in used), avg, health, pension, h_diff, p_diff, candidate
    )


@dataclass(frozen=True)
class LeaveEndRevision:
    """産前産後休業・育児休業等終了時改定の計算結果（SIR-48）。

    changed_*: 従前の標準報酬月額と等級が異なるか（改定の申出の要否は担当者が判断する）
    """

    leave_end: date
    months_considered: tuple[date, ...]
    months_used: tuple[date, ...]
    average: Decimal | None
    health: Grade | None
    pension: Grade | None
    health_changed: bool
    pension_changed: bool
    effective_from: date


def leave_end_revision(
    leave_end: date,
    records: Sequence[MonthlyRemuneration],
    *,
    category: str,
    health_table: GradeTable,
    pension_table: GradeTable | None,
    current_health_standard: Number | None = None,
    current_pension_standard: Number | None = None,
) -> LeaveEndRevision:
    """休業終了日の翌日が属する月以後3か月の報酬から標準報酬月額を算出する。

    支払基礎日数の規則は定時決定と同じものを当てはめる。適用は翌日の属する月から
    4か月目の月分から。
    """
    start = month_start(next_day(leave_end))
    months = tuple(add_months(start, i) for i in range(3))
    if sorted(r.month for r in records) != list(months):
        raise ValueError("終了日の翌日が属する月以後3か月の実績を渡してください")
    used = select_months_by_base_days(records, category)
    avg = average_remuneration(used)
    health = health_table.lookup(avg) if avg is not None else None
    pension = pension_table.lookup(avg) if (avg is not None and pension_table) else None
    h_changed = bool(
        health is not None
        and current_health_standard is not None
        and health.standard != to_decimal(current_health_standard)
    )
    p_changed = bool(
        pension is not None
        and current_pension_standard is not None
        and pension.standard != to_decimal(current_pension_standard)
    )
    return LeaveEndRevision(
        leave_end,
        months,
        tuple(r.month for r in used),
        avg,
        health,
        pension,
        h_changed,
        p_changed,
        add_months(start, 3),
    )
