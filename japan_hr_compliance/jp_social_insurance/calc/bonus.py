"""賞与の保険料（SIR-30〜33、2.5、6.5）。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._util import ZERO, Number, fiscal_year, floor_thousand, to_decimal
from .age import care_insurance_applies, health_loss_date_by_age, pension_loss_date_by_age
from .qualification import Qualification
from .rounding import HealthCareBreakdown, PremiumShare, employer_only, split_health_and_care, split_premium
from .versions import BonusCapVersion, HealthRates, PensionRates

#: 賞与の扱い
TREATMENT_PREMIUM = "premium"
"""保険料を計算する（同月得喪の月を含む）"""
TREATMENT_LOSS_MONTH = "loss_month"
"""喪失月（喪失日前）の支給。保険料はかからないが、健保の年度累計に算入し賞与支払届の対象"""
TREATMENT_NOT_COVERED = "not_covered"
"""資格取得前・資格喪失後の支給。保険料・累計・届出とも対象外"""


def standard_bonus_amount(paid_amount: Number) -> Decimal:
    """標準賞与額（支給額の1,000円未満を切り捨てた額）。"""
    return floor_thousand(paid_amount)


def bonus_treatment(pay_date: date, qualification: Qualification | None) -> str:
    """支給日と資格期間から賞与の扱いを判定する（SIR-33）。"""
    if qualification is None or pay_date < qualification.acquired:
        return TREATMENT_NOT_COVERED
    if qualification.lost is not None and pay_date >= qualification.lost:
        return TREATMENT_NOT_COVERED
    if qualification.is_premium_month(pay_date):
        return TREATMENT_PREMIUM
    return TREATMENT_LOSS_MONTH


def cap_health_bonus(standard_bonus: Number, cumulative_before: Number, annual_cap: Number) -> Decimal:
    """健保の標準賞与額を年度累計の上限で抑える（SIR-31）。

    cumulative_before: 同じ年度の、この支給より前の健保標準賞与額の累計（前職分を含む）
    """
    room = to_decimal(annual_cap) - to_decimal(cumulative_before)
    return max(ZERO, min(to_decimal(standard_bonus), room))


def cap_pension_bonus(standard_bonus: Number, same_month_before: Number, monthly_cap: Number) -> Decimal:
    """厚年の標準賞与額を1か月の上限で抑える（同月の複数回は合算。SIR-32）。

    same_month_before: 同じ月の、この支給より前の厚年標準賞与額の合計
    """
    room = to_decimal(monthly_cap) - to_decimal(same_month_before)
    return max(ZERO, min(to_decimal(standard_bonus), room))


def health_bonus_cumulative(
    prior_bonuses: Iterable[tuple[date, Number]], pay_date: date, prior_employer: Number = 0
) -> Decimal:
    """支給日の年度における、この支給より前の健保標準賞与額の累計（JP Bonus Accumulation）。

    prior_bonuses: (支給日, 健保の標準賞与額) の列。年度が異なるものは数えない（4月1日で0に戻る）
    prior_employer: 同じ年度の前職（協会けんぽ）の累計（手入力）
    """
    fy = fiscal_year(pay_date)
    own = sum(
        (to_decimal(amount) for d, amount in prior_bonuses if fiscal_year(d) == fy and d <= pay_date),
        ZERO,
    )
    return own + to_decimal(prior_employer)


@dataclass(frozen=True)
class BonusPremium:
    """1回の賞与の保険料。

    health_standard_bonus: 上限処理後の健保標準賞与額（喪失月の支給でも累計に算入する額）
    health_cumulative_after: この支給を算入した後の健保の年度累計
    report_required: 賞与支払届の対象か（健保・厚年のいずれかが PREMIUM か LOSS_MONTH）
    保険料がかからない場合（喪失月・免除・対象外）は各保険料が None。
    """

    pay_date: date
    standard_bonus: Decimal
    health_treatment: str
    pension_treatment: str
    health_standard_bonus: Decimal
    pension_standard_bonus: Decimal
    health_cumulative_after: Decimal
    care_applies: bool
    exempt: bool
    health_care: HealthCareBreakdown | None
    support: PremiumShare | None
    pension: PremiumShare | None
    child_contribution: PremiumShare | None

    @property
    def report_required(self) -> bool:
        return TREATMENT_NOT_COVERED != self.health_treatment or TREATMENT_NOT_COVERED != self.pension_treatment

    @property
    def employee_total(self) -> Decimal:
        total = ZERO
        if self.health_care is not None:
            total += self.health_care.combined.employee
        for s in (self.support, self.pension):
            if s is not None:
                total += s.employee
        return total


def calculate_bonus_premium(
    pay_date: date,
    paid_amount: Number,
    *,
    birth_date: date,
    health_rates: HealthRates | None,
    pension_rates: PensionRates | None,
    caps: BonusCapVersion,
    health_qualification: Qualification | None,
    pension_qualification: Qualification | None,
    health_cumulative_before: Number = 0,
    pension_same_month_before: Number = 0,
    exempt: bool = False,
) -> BonusPremium:
    """賞与の保険料を計算する（料率・上限は支給日の属する月の版を呼び出し側で選ぶ。5.2）。

    - 健保は年度累計の上限、厚年は月の上限（同月合算）で標準賞与額を抑える
    - 喪失月の支給は保険料なし・健保累計に算入、資格喪失後は対象外、同月得喪は保険料あり
    - 75歳（健保）・70歳（厚年）の年齢到達による喪失も資格期間に反映する
    - 介護・支援金は健保の標準賞与額、拠出金は厚年の標準賞与額に料率を掛ける
    - exempt: 産休・育休による賞与の免除（exemption.is_bonus_exempt）。免除でも健保累計には算入する
    """
    std = standard_bonus_amount(paid_amount)
    hq = health_qualification.with_loss_no_later_than(health_loss_date_by_age(birth_date)) if health_qualification else None
    pq = (
        pension_qualification.with_loss_no_later_than(pension_loss_date_by_age(birth_date))
        if pension_qualification
        else None
    )
    h_treat = bonus_treatment(pay_date, hq)
    p_treat = bonus_treatment(pay_date, pq)

    cumulative = to_decimal(health_cumulative_before)
    h_std = ZERO
    if h_treat != TREATMENT_NOT_COVERED:
        h_std = cap_health_bonus(std, cumulative, caps.health_annual_cap)
        cumulative += h_std
    p_std = ZERO
    if p_treat != TREATMENT_NOT_COVERED:
        p_std = cap_pension_bonus(std, pension_same_month_before, caps.pension_monthly_cap)

    care = h_treat == TREATMENT_PREMIUM and care_insurance_applies(birth_date, pay_date)
    health_care = support = pension = child = None
    if not exempt and h_treat == TREATMENT_PREMIUM:
        if health_rates is None:
            raise ValueError("健保の料率が渡されていません")
        health_care = split_health_and_care(h_std, health_rates.health, health_rates.care if care else None)
        if health_rates.support > 0:
            support = split_premium(h_std, health_rates.support)
    if not exempt and p_treat == TREATMENT_PREMIUM:
        if pension_rates is None:
            raise ValueError("厚年の料率が渡されていません")
        pension = split_premium(p_std, pension_rates.pension)
        child = employer_only(p_std, pension_rates.child_contribution)

    return BonusPremium(
        pay_date=pay_date,
        standard_bonus=std,
        health_treatment=h_treat,
        pension_treatment=p_treat,
        health_standard_bonus=h_std,
        pension_standard_bonus=p_std,
        health_cumulative_after=cumulative,
        care_applies=care,
        exempt=exempt,
        health_care=health_care,
        support=support,
        pension=pension,
        child_contribution=child,
    )


def counts_as_remuneration(annual_payments_by_rule: int | None) -> bool:
    """賃金規程上の年間支給回数が4回以上なら報酬（賞与ではない）として扱う（SIR-33）。

    None（規程で回数が明らかでない）は False を返し、実績による警告で判定する。
    """
    return annual_payments_by_rule is not None and annual_payments_by_rule >= 4


def bonus_frequency_warning(pay_dates: Iterable[date], july_first: date) -> bool:
    """規程で回数が明らかでない支給項目で、7月1日以前1年間の支給が4回以上なら True（SIR-33）。

    期間は前年7月2日から当年7月1日まで。
    """
    if (july_first.month, july_first.day) != (7, 1):
        raise ValueError("基準日は7月1日を渡してください")
    start = date(july_first.year - 1, 7, 2)
    return sum(1 for d in pay_dates if start <= d <= july_first) >= 4
