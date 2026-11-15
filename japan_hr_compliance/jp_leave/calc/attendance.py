"""出勤率の算定。LVR-10〜16、規則 2.3・5.2・5.5。

全労働日＝算定期間の所定労働日（会社カレンダー・シフト）から、次の4つを除いた日。
所定休日に労働した日（所定労働日ではないので最初から数えない）、不可抗力による休業日、
使用者側に起因する経営・管理上の障害による休業日、正当な争議行為の日。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from fractions import Fraction
from typing import Dict, Iterable, Mapping, Optional, Tuple

from .dates import ONE_DAY


class DayStatus(str, Enum):
    """日次の勤怠区分（B-2 の日次の勤怠区分を写す）。"""

    WORKED = "出勤"  # 遅刻・早退、半日年休の日を含む
    ANNUAL_LEAVE = "年次有給休暇"
    OCCUPATIONAL_INJURY = "業務上の傷病による休業"
    MATERNITY = "産前産後休業"
    CHILDCARE = "育児休業"  # 出生時育児休業を含む
    FAMILY_CARE = "介護休業"
    NOT_ATTRIBUTABLE = "労働者の責に帰すべき事由によらない不就労"
    FORCE_MAJEURE = "不可抗力による休業"
    EMPLOYER_CAUSED = "使用者側に起因する経営・管理上の障害による休業"
    STRIKE = "正当な争議行為"
    OTHER_LEAVE = "法令に定めのない休暇"  # leave_type で種別を指定
    ABSENT = "欠勤"
    UNRECORDED = "未確定"


class LeaveTreatment(str, Enum):
    """法令に定めのない休暇の出勤率での扱い（LVR-13）。"""

    DEEMED = "出勤とみなす"
    EXCLUDED = "全労働日から除く"
    ABSENT = "欠勤"


#: 法定のみなし出勤（39条10項）と行政解釈による年休
DEEMED_STATUSES = (
    DayStatus.ANNUAL_LEAVE,
    DayStatus.OCCUPATIONAL_INJURY,
    DayStatus.MATERNITY,
    DayStatus.CHILDCARE,
    DayStatus.FAMILY_CARE,
    DayStatus.NOT_ATTRIBUTABLE,
)

#: 全労働日から除く日（所定休日の労働を除く3つ。除外はこれと所定休日労働の4つに限る）
EXCLUDED_STATUSES = (DayStatus.FORCE_MAJEURE, DayStatus.EMPLOYER_CAUSED, DayStatus.STRIKE)

HOLIDAY_WORK = "所定休日に労働した日"
SHORTENED_NOTE = "前倒しにより短縮された期間の所定労働日は出勤したものとみなし、分子・分母に算入した"
SHORTENED_KEY = "前倒しで短縮された期間"


@dataclass(frozen=True)
class DayRecord:
    """1日の勤怠。``status`` が OTHER_LEAVE のときは ``leave_type`` に休暇種別名を入れる。"""

    status: DayStatus
    leave_type: Optional[str] = None


@dataclass(frozen=True)
class AttendanceRateResult:
    """出勤率の算定明細（JP Attendance Rate Calc に保存する。LVR-15）。"""

    period_start: date
    period_end: date
    scheduled_days: int  # 実際の算定期間の会社カレンダー上の所定労働日（短縮された期間を含まない）
    excluded: Dict[str, int]  # 全労働日から除いた日（区分別）
    holiday_work_days: int  # 所定休日に労働した日（分母・分子に入れない）
    total_working_days: int  # 全労働日（分母）
    worked_days: int  # 実際に出勤した日
    deemed: Dict[str, int]  # みなし出勤（区分別）
    attended_days: int  # 出勤日（分子）
    absent_days: int
    absent_breakdown: Dict[str, int]
    unrecorded_days: Tuple[date, ...]
    treatments: Dict[str, str]  # 使用した休暇種別設定の写し
    shortened_days: int = 0  # 短縮された期間の暦日数
    shortened_deemed_days: int = 0  # うち所定労働日（出勤とみなして分子・分母に算入）
    notes: Tuple[str, ...] = ()

    @property
    def is_pending(self) -> bool:
        """勤怠が未確定の日がある（付与予定を保留にする。LVR-16）。"""
        return bool(self.unrecorded_days)

    @property
    def rate(self) -> Optional[Fraction]:
        """出勤率（丸めない分数）。全労働日が 0 なら None。"""
        if self.total_working_days == 0:
            return None
        return Fraction(self.attended_days, self.total_working_days)

    @property
    def eligible(self) -> Optional[bool]:
        """8割以上なら True、未満なら False。保留または判定不能なら None。

        算定期間が前倒しで全部短縮された（期間が空の）場合は、全期間出勤とみなして True。
        """
        if self.is_pending:
            return None
        if self.period_end < self.period_start:
            return True
        if self.total_working_days == 0:
            return None
        return meets_eighty_percent(self.attended_days, self.total_working_days)


def meets_eighty_percent(attended_days: int, total_working_days: int) -> bool:
    """8割判定。割り算を丸めずに「出勤日数×10 ≧ 全労働日×8」で行う（LVR-14）。"""
    return attended_days * 10 >= total_working_days * 8


def attendance_period(hire_date: date, base_date: date, previous_base_date: Optional[date] = None) -> Tuple[date, date]:
    """出勤率の算定期間（LVR-10）。

    初回は雇入れ日から初回基準日の前日まで、以後は前回基準日から今回基準日の前日まで。
    斉一的付与・前倒しでは ``previous_base_date`` に前回の付与日を渡す（規則 5.5）。
    """
    start = previous_base_date if previous_base_date is not None else hire_date
    return start, base_date - ONE_DAY


def calc_attendance_rate(
    period_start: date,
    period_end: date,
    scheduled_days: Iterable[date],
    records: Mapping[date, DayRecord],
    leave_treatments: Optional[Mapping[str, LeaveTreatment]] = None,
    legal_period: Optional[Tuple[date, date]] = None,
) -> AttendanceRateResult:
    """出勤率を算定する（LVR-11〜15）。

    Args:
        period_start: 算定期間の初日。
        period_end: 算定期間の末日（当日を含む）。前倒しで期間が空なら ``period_start`` より前でよい。
        scheduled_days: 会社カレンダー・シフト上の所定労働日。算定期間外の日は無視する。ただし
            ``legal_period`` を渡す場合は、短縮された期間の所定労働日も含めて渡す。
        records: 日ごとの勤怠。所定労働日で記録がない日は「未確定」として保留の理由になる。
            所定休日の記録は、出勤（WORKED）なら「所定休日に労働した日」として明細に数えるだけで、
            分母・分子には入れない。
        leave_treatments: 法令に定めのない休暇の種別ごとの扱い。未設定の種別は「出勤とみなす」。
        legal_period: 前倒し付与の場合の法定の算定期間。法定の算定期間のうち実際の算定期間に
            含まれない部分を「短縮された期間」とし、その所定労働日を出勤したものとみなして
            分子・分母の両方に加える（規則 2.4 ①）。

    Returns:
        算定明細。
    """
    treatments = dict(leave_treatments or {})
    excluded: Dict[str, int] = {}
    deemed: Dict[str, int] = {}
    absent: Dict[str, int] = {}
    unrecorded = []
    scheduled = 0
    worked = 0
    used_treatments: Dict[str, str] = {}

    all_scheduled = set(scheduled_days)
    scheduled_set = {d for d in all_scheduled if period_start <= d <= period_end}
    for d in sorted(scheduled_set):
        scheduled += 1
        rec = records.get(d)
        status = rec.status if rec else DayStatus.UNRECORDED
        if status == DayStatus.UNRECORDED:
            unrecorded.append(d)
        elif status == DayStatus.WORKED:
            worked += 1
        elif status in DEEMED_STATUSES:
            deemed[status.value] = deemed.get(status.value, 0) + 1
        elif status in EXCLUDED_STATUSES:
            excluded[status.value] = excluded.get(status.value, 0) + 1
        elif status == DayStatus.OTHER_LEAVE:
            name = rec.leave_type or "（種別未指定）"
            treatment = treatments.get(name, LeaveTreatment.DEEMED)
            used_treatments[name] = treatment.value
            key = f"休暇：{name}"
            if treatment == LeaveTreatment.DEEMED:
                deemed[key] = deemed.get(key, 0) + 1
            elif treatment == LeaveTreatment.EXCLUDED:
                excluded[key] = excluded.get(key, 0) + 1
            else:
                absent[key] = absent.get(key, 0) + 1
        elif status == DayStatus.ABSENT:
            absent[status.value] = absent.get(status.value, 0) + 1
        else:  # pragma: no cover - 列挙の追加漏れの検出
            raise ValueError(f"未対応の勤怠区分: {status}")

    holiday_work = sum(
        1
        for d, rec in records.items()
        if period_start <= d <= period_end and d not in scheduled_set and rec.status == DayStatus.WORKED
    )

    shortened = 0
    shortened_deemed = 0
    notes = []
    if legal_period is not None:
        legal_start, legal_end = legal_period
        shortened = sum(
            1
            for i in range((legal_end - legal_start).days + 1)
            if not (period_start <= legal_start + timedelta(days=i) <= period_end)
        )
        shortened_deemed = sum(
            1 for d in all_scheduled if legal_start <= d <= legal_end and not (period_start <= d <= period_end)
        )
        if shortened:
            notes.append(SHORTENED_NOTE)
        if shortened_deemed:
            deemed[SHORTENED_KEY] = shortened_deemed

    n_excluded = sum(excluded.values())
    n_deemed = sum(deemed.values())
    n_absent = sum(absent.values())
    total = scheduled - n_excluded + shortened_deemed
    attended = worked + n_deemed

    return AttendanceRateResult(
        period_start=period_start,
        period_end=period_end,
        scheduled_days=scheduled,
        excluded=excluded,
        holiday_work_days=holiday_work,
        total_working_days=total,
        worked_days=worked,
        deemed=deemed,
        attended_days=attended,
        absent_days=n_absent,
        absent_breakdown=absent,
        unrecorded_days=tuple(unrecorded),
        treatments=used_treatments,
        shortened_days=shortened,
        shortened_deemed_days=shortened_deemed,
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class TreatmentChangeWarning:
    """会社が承認・付与する休暇を「欠勤」に設定するときの警告（LVR-13）。

    設定者・日時の記録は呼び出し側（DocType）で行う。
    """

    leave_type: str
    message: str


def check_treatment_change(
    leave_type: str, new_treatment: LeaveTreatment, company_approved: bool = True
) -> Optional[TreatmentChangeWarning]:
    """休暇種別の出勤率での扱いを変更するときの警告を返す。警告がなければ None。"""
    if new_treatment == LeaveTreatment.ABSENT and company_approved:
        return TreatmentChangeWarning(
            leave_type,
            f"「{leave_type}」は会社が承認・付与する休暇です。出勤率の算定で欠勤扱いにすると、"
            "不利益取扱いとなるおそれがあります（労基法39条の趣旨）。",
        )
    return None
