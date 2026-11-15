"""端数処理と本人負担・事業主負担の分割（SIR-20〜22、SIR-30、SIR-60）。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ._util import HALF, ONE, ZERO, Number, floor_yen, to_decimal


def round_50sen(amount: Number) -> Decimal:
    """被保険者負担分の50銭ルールで円にする。

    1円未満の端数が50銭以下なら切り捨て、50銭を超えれば切り上げる
    （給与から控除する場合の取扱い。2.2）。50銭ちょうどは切り捨てる。

    >>> round_50sen(Decimal("9749.50"))
    Decimal('9749')
    >>> round_50sen(Decimal("9749.51"))
    Decimal('9750')
    """
    value = to_decimal(amount)
    if value < 0:
        raise ValueError("負の金額は扱えません")
    yen = floor_yen(value)
    return yen + ONE if value - yen > HALF else yen


@dataclass(frozen=True)
class PremiumShare:
    """1つの保険料の全額・本人負担・事業主負担。

    total: 基礎額 × 料率（銭以下を含む正確な値）
    employee: 本人負担（円。50銭ルール適用後）
    employer: 事業主負担 ＝ total − employee（銭以下を含みうる。2.2・SIR-27）
    """

    total: Decimal
    employee: Decimal
    employer: Decimal

    @property
    def half(self) -> Decimal:
        """折半額（total ÷ 2）。"""
        return self.total / 2

    @property
    def employee_exceeds_employer(self) -> bool:
        """本人負担が事業主負担を上回るか（2.2 の検算用）。"""
        return self.employee > self.employer

    def __add__(self, other: PremiumShare) -> PremiumShare:
        return PremiumShare(
            self.total + other.total,
            self.employee + other.employee,
            self.employer + other.employer,
        )


ZERO_SHARE = PremiumShare(ZERO, ZERO, ZERO)


def split_premium(base: Number, rate: Number) -> PremiumShare:
    """基礎額（標準報酬月額・標準賞与額）× 料率を折半し、本人負担を50銭ルールで求める。

    rate は小数（11.47% は Decimal("0.1147")）で渡す。
    """
    total = to_decimal(base) * to_decimal(rate)
    employee = round_50sen(total / 2)
    return PremiumShare(total, employee, total - employee)


def employer_only(base: Number, rate: Number) -> PremiumShare:
    """事業主のみが負担する保険料（子ども・子育て拠出金、労災）。端数は丸めない。"""
    total = to_decimal(base) * to_decimal(rate)
    return PremiumShare(total, ZERO, total)


@dataclass(frozen=True)
class HealthCareBreakdown:
    """健保＋介護の合算処理と、別行表示用の按分（SIR-21）。

    combined: 合算の料率（健保率＋介護率）で端数処理した額。控除額はこれ
    health: 健保率のみで端数処理した額（別行表示の健保行）
    care: combined − health（別行表示の介護行）。介護非該当なら None
    """

    combined: PremiumShare
    health: PremiumShare
    care: PremiumShare | None


def split_health_and_care(
    base: Number, health_rate: Number, care_rate: Number | None
) -> HealthCareBreakdown:
    """健保と介護を合算して端数処理し、別行表示用に按分する。

    care_rate が None（介護非該当）の場合は健保のみで計算する。
    """
    health = split_premium(base, health_rate)
    if care_rate is None:
        return HealthCareBreakdown(combined=health, health=health, care=None)
    combined = split_premium(base, to_decimal(health_rate) + to_decimal(care_rate))
    care = PremiumShare(
        combined.total - health.total,
        combined.employee - health.employee,
        combined.employer - health.employer,
    )
    return HealthCareBreakdown(combined=combined, health=health, care=care)
