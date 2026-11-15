"""短時間労働者の社会保険・雇用保険の適用判定（SIR-70〜73、2.9）。

資格の取得・喪失は担当者が確定する。この関数は区分を示すだけで自動変更しない。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._util import Number, add_months, month_start, to_decimal
from .versions import CONFIRMED, select_version

COVERAGE_GENERAL = "general"
COVERAGE_SHORT_TIME = "short_time"
COVERAGE_NONE = "none"


@dataclass(frozen=True)
class EligibilityRule:
    """適用判定条件の版（JP SI Eligibility Rule）。effective_from は施行日。

    wage_requirement: 賃金要件の有無（2026年10月撤廃）
    company_size_threshold: 企業規模要件の人数（厚年被保険者数。警告の基準）
    ei_weekly_hours: 雇用保険の週所定労働時間の基準（雇用保険の判定専用）
    """

    effective_from: date
    wage_requirement: bool
    wage_threshold: Decimal | None
    company_size_threshold: int | None
    ei_weekly_hours: Decimal
    sh_weekly_hours: Decimal = Decimal("20")
    status: str = CONFIRMED

    def __post_init__(self) -> None:
        if self.wage_threshold is not None:
            object.__setattr__(self, "wage_threshold", to_decimal(self.wage_threshold))
        object.__setattr__(self, "ei_weekly_hours", to_decimal(self.ei_weekly_hours))
        object.__setattr__(self, "sh_weekly_hours", to_decimal(self.sh_weekly_hours))


def classify_social_insurance(
    judgment_date: date,
    rules: Iterable[EligibilityRule],
    *,
    weekly_hours: Number,
    monthly_days: Number,
    normal_weekly_hours: Number,
    normal_monthly_days: Number,
    monthly_wage: Number,
    expected_over_two_months: bool,
    is_student: bool,
    specific_applicable_office: bool,
) -> str:
    """判定日時点の社保の適用区分（一般／短時間労働者／適用なし）を返す（SIR-70・71）。

    4分の3基準：週の所定労働時間と月の所定労働日数がともに通常の労働者の4分の3以上なら一般。
    4分の3未満：特定適用事業所で、週20時間以上・（賃金要件があれば）賃金が基準以上・
    2か月を超える雇用見込み・学生でない、のすべてを満たせば短時間労働者。
    specific_applicable_office は担当者が届出後に登録した値（自動判定しない）。
    """
    rule = select_version(rules, judgment_date, by_month=False)
    three_quarters = Decimal(3) / Decimal(4)
    if to_decimal(weekly_hours) >= to_decimal(normal_weekly_hours) * three_quarters and to_decimal(
        monthly_days
    ) >= to_decimal(normal_monthly_days) * three_quarters:
        return COVERAGE_GENERAL
    if not specific_applicable_office:
        return COVERAGE_NONE
    if to_decimal(weekly_hours) < rule.sh_weekly_hours:
        return COVERAGE_NONE
    if rule.wage_requirement:
        if rule.wage_threshold is None:
            raise ValueError("賃金要件の額が登録されていません")
        if to_decimal(monthly_wage) < rule.wage_threshold:
            return COVERAGE_NONE
    if not expected_over_two_months or is_student:
        return COVERAGE_NONE
    return COVERAGE_SHORT_TIME


def classify_employment_insurance(
    judgment_date: date,
    rules: Iterable[EligibilityRule],
    *,
    weekly_hours: Number,
    expected_31_days_or_more: bool,
    excluded_student: bool,
) -> bool:
    """雇用保険の被保険者に該当するか（週所定時間の基準以上・31日以上の見込み。SIR-73）。"""
    rule = select_version(rules, judgment_date, by_month=False)
    return (
        to_decimal(weekly_hours) >= rule.ei_weekly_hours
        and expected_31_days_or_more
        and not excluded_student
    )


def headcount_warning(
    monthly_counts: Mapping[date, int],
    threshold: int,
    as_of_month: date,
    *,
    window_months: int = 12,
    min_months: int = 6,
) -> bool:
    """厚年被保険者数（短時間労働者を除く）が基準以上の月が、直近 window_months か月のうち
    min_months か月以上あれば警告する（SIR-71。特定適用事業所の該当は担当者が登録する）。
    """
    counts = {month_start(k): v for k, v in monthly_counts.items()}
    end = month_start(as_of_month)
    months = [add_months(end, -i) for i in range(window_months)]
    return sum(1 for m in months if counts.get(m, 0) >= threshold) >= min_months
