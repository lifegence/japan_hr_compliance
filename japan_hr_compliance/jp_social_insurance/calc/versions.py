"""料率・上限・等級表の版と、版の選び方（SIR-01〜06、5.2・5.3）。

版のデータは呼び出し側（DocType 等）から渡す。このモジュールは DB に触らず、
値も埋め込まない。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, TypeVar

from ._util import ZERO, month_start, to_decimal

DRAFT = "draft"
CONFIRMED = "confirmed"

#: 保険者区分。第1次は協会けんぽのみ（SIR-02）
KYOKAI_KENPO = "kyokai_kenpo"


class VersionNotFoundError(LookupError):
    """対象の月・日に有効な確認済の版がない。"""


class AmbiguousVersionError(LookupError):
    """同じ適用開始の確認済の版が複数ある。"""


class _Versioned(Protocol):
    effective_from: date
    status: str


V = TypeVar("V", bound=_Versioned)


def select_version(
    versions: Iterable[V],
    target: date,
    *,
    by_month: bool = True,
    predicate: Callable[[V], bool] | None = None,
) -> V:
    """対象の月（または日）に有効な版を選ぶ（5.2）。

    確認済（status == CONFIRMED）の版のうち、適用開始がその月以前で最も新しいものを返す。
    版は終了日を持たず、次の版の適用開始の前月（前日）まで有効とする。

    by_month=True: 社会保険の料率・等級表。適用開始と対象を年月で比べる
    by_month=False: 雇用保険料率・判定条件。適用開始日と基準日を日で比べる
    predicate: 事業の種類などで候補を絞る条件
    """
    key = month_start if by_month else (lambda d: d)
    target_key = key(target)
    candidates = [
        v
        for v in versions
        if v.status == CONFIRMED
        and key(v.effective_from) <= target_key
        and (predicate is None or predicate(v))
    ]
    if not candidates:
        raise VersionNotFoundError(f"{target_key.isoformat()} に有効な確認済の版がありません")
    latest = max(key(v.effective_from) for v in candidates)
    chosen = [v for v in candidates if key(v.effective_from) == latest]
    if len(chosen) > 1:
        raise AmbiguousVersionError(
            f"適用開始 {latest.isoformat()} の確認済の版が {len(chosen)} 件あります"
        )
    return chosen[0]


@dataclass(frozen=True)
class HealthRates:
    """1都道府県の健保率・介護率・支援金率（小数。9.85% は Decimal("0.0985")）。

    support が 0 の版では支援金を計上しない（2026年3月分以前）。
    """

    health: Decimal
    care: Decimal
    support: Decimal = ZERO

    def __post_init__(self) -> None:
        for name in ("health", "care", "support"):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))


@dataclass(frozen=True)
class HealthInsuranceRateVersion:
    """健康保険料率の版（JP Health Insurance Rate）。都道府県 → 料率。"""

    effective_from: date
    rates: Mapping[str, HealthRates]
    status: str = CONFIRMED
    insurer: str = KYOKAI_KENPO
    name: str = ""

    def __post_init__(self) -> None:
        if self.insurer != KYOKAI_KENPO:
            raise ValueError("第1次の保険者は協会けんぽのみです（SIR-02）")

    def rates_for(self, prefecture: str) -> HealthRates:
        """都道府県の料率を返す。登録がなければ VersionNotFoundError。"""
        try:
            return self.rates[prefecture]
        except KeyError:
            raise VersionNotFoundError(
                f"版 {self.effective_from.isoformat()} に {prefecture} の料率がありません"
            ) from None


@dataclass(frozen=True)
class PensionRates:
    """厚年率と子ども・子育て拠出金率（小数）。"""

    pension: Decimal
    child_contribution: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "pension", to_decimal(self.pension))
        object.__setattr__(self, "child_contribution", to_decimal(self.child_contribution))


@dataclass(frozen=True)
class PensionRateVersion:
    """厚生年金・拠出金率の版（JP Pension Rate）。"""

    effective_from: date
    rates: PensionRates
    status: str = CONFIRMED
    name: str = ""


@dataclass(frozen=True)
class BonusCapVersion:
    """賞与の上限の版（JP Bonus Cap）。"""

    effective_from: date
    health_annual_cap: Decimal
    pension_monthly_cap: Decimal
    status: str = CONFIRMED

    def __post_init__(self) -> None:
        object.__setattr__(self, "health_annual_cap", to_decimal(self.health_annual_cap))
        object.__setattr__(self, "pension_monthly_cap", to_decimal(self.pension_monthly_cap))


@dataclass(frozen=True)
class EmploymentInsuranceRateVersion:
    """雇用保険料率の版（JP Employment Insurance Rate）。率は1000分率で保持する。

    effective_from は適用開始日（通常は年度の4月1日）。
    """

    effective_from: date
    business_type: str
    employee_permille: Decimal
    employer_permille: Decimal
    status: str = CONFIRMED

    def __post_init__(self) -> None:
        object.__setattr__(self, "employee_permille", to_decimal(self.employee_permille))
        object.__setattr__(self, "employer_permille", to_decimal(self.employer_permille))

    @property
    def employee_rate(self) -> Decimal:
        return self.employee_permille / 1000

    @property
    def employer_rate(self) -> Decimal:
        return self.employer_permille / 1000


@dataclass(frozen=True)
class StandardRemuneration:
    """標準報酬月額の履歴1件（JP Standard Remuneration History。SIR-12）。

    等級番号ではなく金額で保持する（5.2）。pension_standard は厚年の被保険者でなければ None。
    confirmed=False（担当者の確定前）の行は保険料計算に使わない（SIR-46）。
    """

    effective_from: date
    health_standard: Decimal | None
    pension_standard: Decimal | None
    decision_type: str = ""
    confirmed: bool = True

    def __post_init__(self) -> None:
        for name in ("health_standard", "pension_standard"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, to_decimal(value))


def select_standard_remuneration(
    history: Sequence[StandardRemuneration], target_month: date
) -> StandardRemuneration:
    """対象月に有効な標準報酬月額を返す（確定済の行のうち適用開始月が以前で最も新しいもの）。"""
    target = month_start(target_month)
    candidates = [h for h in history if h.confirmed and month_start(h.effective_from) <= target]
    if not candidates:
        raise VersionNotFoundError(f"{target.isoformat()} に有効な標準報酬月額がありません")
    latest = max(month_start(h.effective_from) for h in candidates)
    chosen = [h for h in candidates if month_start(h.effective_from) == latest]
    if len(chosen) > 1:
        raise AmbiguousVersionError(f"適用開始 {latest.isoformat()} の標準報酬月額が複数あります")
    return chosen[0]
