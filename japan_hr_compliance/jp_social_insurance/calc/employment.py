"""雇用保険料（SIR-60〜63、2.8、6.8）。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._util import ZERO, Number, to_decimal
from .rounding import round_50sen
from .versions import EmploymentInsuranceRateVersion, select_version

#: 料率の切替基準（SIR-60）
BASIS_CLOSING_DATE = "closing_date"
"""賃金締切日（既定。賃金支払確定日＝締切日）"""
BASIS_PAYMENT_DATE = "payment_date"
"""支給日（支給の時期が混在する会社）"""


def employment_insurance_target_wage(items: Iterable[tuple[Number, bool]]) -> Decimal:
    """支給項目（金額, 雇用保険の対象か）から対象賃金を集計する（SIR-62）。

    通勤手当は対象、退職金・慶弔見舞金・出張旅費等は対象外（区分は Salary Component 側）。
    """
    return sum((to_decimal(amount) for amount, target in items if target), ZERO)


def employment_insurance_basis_date(
    *,
    closing_date: date | None,
    payment_date: date,
    basis: str = BASIS_CLOSING_DATE,
    is_bonus: bool = False,
) -> date:
    """料率の版を選ぶ基準日（SIR-60）。賞与は常に支給日。"""
    if is_bonus or basis == BASIS_PAYMENT_DATE:
        return payment_date
    if basis != BASIS_CLOSING_DATE:
        raise ValueError(f"切替基準が不正です: {basis}")
    if closing_date is None:
        raise ValueError("賃金締切日が必要です")
    return closing_date


def select_employment_insurance_rate(
    versions: Iterable[EmploymentInsuranceRateVersion],
    business_type: str,
    *,
    closing_date: date | None,
    payment_date: date,
    basis: str = BASIS_CLOSING_DATE,
    is_bonus: bool = False,
) -> EmploymentInsuranceRateVersion:
    """事業の種類と切替基準で雇用保険料率の版を選ぶ（確認済・適用開始日が基準日以前で最新）。"""
    basis_date = employment_insurance_basis_date(
        closing_date=closing_date, payment_date=payment_date, basis=basis, is_bonus=is_bonus
    )
    return select_version(
        versions, basis_date, by_month=False, predicate=lambda v: v.business_type == business_type
    )


@dataclass(frozen=True)
class EmploymentInsurancePremium:
    """雇用保険料。employee は50銭ルール後の円、employer は 対象賃金×事業主負担率（丸めない）。"""

    target_wage: Decimal
    employee: Decimal
    employer: Decimal


def calculate_employment_insurance(
    target_wage: Number,
    version: EmploymentInsuranceRateVersion | None,
    *,
    insured: bool = True,
    is_officer: bool = False,
) -> EmploymentInsurancePremium:
    """雇用保険料を計算する（SIR-60・61・63）。

    被保険者でない者・役員（兼務役員は労働者部分の賃金だけを target_wage に渡す）は0円。
    年齢による免除は行わない。産休・育休の免除は適用しない（賃金がなければ0円）。
    """
    wage = to_decimal(target_wage)
    if not insured or is_officer or wage == 0:
        return EmploymentInsurancePremium(wage, ZERO, ZERO)
    if version is None:
        raise ValueError("雇用保険料率の版が渡されていません")
    return EmploymentInsurancePremium(
        wage, round_50sen(wage * version.employee_rate), wage * version.employer_rate
    )
