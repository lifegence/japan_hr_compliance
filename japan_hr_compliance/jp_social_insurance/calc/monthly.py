"""月額保険料の計算（SIR-20〜27、5.2、6.1）。"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._util import ZERO, Number, floor_yen, month_start, to_decimal
from .age import care_insurance_applies, health_applies_by_age, pension_applies_by_age
from .grades import GradeTable
from .qualification import DeductionPlan, Qualification, plan_deduction
from .rounding import (
    HealthCareBreakdown,
    PremiumShare,
    employer_only,
    split_health_and_care,
    split_premium,
)
from .versions import (
    HealthInsuranceRateVersion,
    HealthRates,
    PensionRates,
    PensionRateVersion,
    StandardRemuneration,
    select_standard_remuneration,
    select_version,
)

#: 健保・介護の明細表示（SIR-21）
DISPLAY_COMBINED = "combined"
DISPLAY_SEPARATE = "separate"


@dataclass(frozen=True)
class MonthlyPremium:
    """1か月分（対象月）の社会保険料。

    該当しない保険料は None。免除月（exempt=True）はすべて None（0円）になる（SIR-52）。
    support は版の支援金率が 0 の月（2026年3月分以前）は None。
    """

    target_month: date
    health_standard: Decimal | None
    pension_standard: Decimal | None
    care_applies: bool
    exempt: bool
    health_care: HealthCareBreakdown | None
    support: PremiumShare | None
    pension: PremiumShare | None
    child_contribution: PremiumShare | None

    def _shares(self) -> list[PremiumShare]:
        shares = []
        if self.health_care is not None:
            shares.append(self.health_care.combined)
        for s in (self.support, self.pension, self.child_contribution):
            if s is not None:
                shares.append(s)
        return shares

    @property
    def employee_total(self) -> Decimal:
        """本人負担の合計（円）。"""
        return sum((s.employee for s in self._shares()), ZERO)

    @property
    def employer_total(self) -> Decimal:
        """事業主負担の合計（拠出金を含む。銭以下を含みうる）。"""
        return sum((s.employer for s in self._shares()), ZERO)


def calculate_monthly_premium(
    target_month: date,
    *,
    birth_date: date,
    health_standard: Number | None,
    pension_standard: Number | None,
    health_rates: HealthRates | None,
    pension_rates: PensionRates | None,
    health_insured: bool = True,
    pension_insured: bool = True,
    exempt: bool = False,
) -> MonthlyPremium:
    """対象月の保険料を計算する（料率・標準報酬月額は呼び出し側で対象月のものを選んで渡す）。

    - 健保＋介護は合算の料率で端数処理し、別行表示用に按分する（SIR-21）
    - 支援金は健保とは別に端数処理する（SIR-22）
    - 介護は40歳到達月〜65歳到達月の前月、健保は75歳誕生月の前月、厚年・拠出金は
      70歳到達月の前月まで（SIR-13）
    - health_insured / pension_insured は対象月が資格期間内か（Qualification.is_premium_month）
    """
    month = month_start(target_month)
    h_on = health_insured and health_standard is not None and health_applies_by_age(birth_date, month)
    p_on = pension_insured and pension_standard is not None and pension_applies_by_age(birth_date, month)
    care = h_on and care_insurance_applies(birth_date, month)
    h_std = to_decimal(health_standard) if h_on else None
    p_std = to_decimal(pension_standard) if p_on else None

    if exempt:
        return MonthlyPremium(month, h_std, p_std, care, True, None, None, None, None)

    health_care = support = pension = child = None
    if h_std is not None:
        if health_rates is None:
            raise ValueError("健保の料率が渡されていません")
        health_care = split_health_and_care(h_std, health_rates.health, health_rates.care if care else None)
        if health_rates.support > 0:
            support = split_premium(h_std, health_rates.support)
    if p_std is not None:
        if pension_rates is None:
            raise ValueError("厚年の料率が渡されていません")
        pension = split_premium(p_std, pension_rates.pension)
        child = employer_only(p_std, pension_rates.child_contribution)
    return MonthlyPremium(month, h_std, p_std, care, False, health_care, support, pension, child)


@dataclass(frozen=True)
class StatementLine:
    """給与明細の控除1行。key は health_care / health / care / support / pension。"""

    key: str
    employee: Decimal
    employer: Decimal


def statement_lines(
    premium: MonthlyPremium,
    *,
    care_display: str = DISPLAY_COMBINED,
    support_display: str = DISPLAY_SEPARATE,
) -> list[StatementLine]:
    """明細の表示行を作る（端数処理は表示方法によらず同じ。SIR-21・22）。

    care_display: 健保・介護を「健康保険料（介護含む）」1行（既定）か別行か
    support_display: 支援金を別行（既定）か健保の行に合算か
    拠出金は事業主のみのため明細行に含めない。
    """
    for value in (care_display, support_display):
        if value not in (DISPLAY_COMBINED, DISPLAY_SEPARATE):
            raise ValueError(f"表示方法が不正です: {value}")
    lines: list[StatementLine] = []
    hc = premium.health_care
    if hc is not None:
        if care_display == DISPLAY_SEPARATE and hc.care is not None:
            lines.append(StatementLine("health", hc.health.employee, hc.health.employer))
            lines.append(StatementLine("care", hc.care.employee, hc.care.employer))
        else:
            lines.append(StatementLine("health_care", hc.combined.employee, hc.combined.employer))
    if premium.support is not None:
        if support_display == DISPLAY_COMBINED and lines:
            first = lines[0]
            lines[0] = StatementLine(
                first.key,
                first.employee + premium.support.employee,
                first.employer + premium.support.employer,
            )
        else:
            lines.append(StatementLine("support", premium.support.employee, premium.support.employer))
    if premium.pension is not None:
        lines.append(StatementLine("pension", premium.pension.employee, premium.pension.employer))
    return lines


@dataclass(frozen=True)
class PayDeduction:
    """1回の給与での社会保険料の控除（対象月ごとの保険料）。"""

    plan: DeductionPlan
    premiums: tuple[MonthlyPremium, ...]

    @property
    def employee_total(self) -> Decimal:
        return sum((p.employee_total for p in self.premiums), ZERO)


def calculate_premium_for_month(
    target_month: date,
    *,
    birth_date: date,
    prefecture: str,
    health_qualification: Qualification | None,
    pension_qualification: Qualification | None,
    standard_history: Sequence[StandardRemuneration],
    health_rate_versions: Iterable[HealthInsuranceRateVersion],
    pension_rate_versions: Iterable[PensionRateVersion],
    exempt_months: Collection[date] = (),
) -> MonthlyPremium:
    """対象月の版・標準報酬月額を選んで保険料を計算する（5.2。支給月・控除月では選ばない）。"""
    month = month_start(target_month)
    h_ins = health_qualification is not None and health_qualification.is_premium_month(month)
    p_ins = pension_qualification is not None and pension_qualification.is_premium_month(month)
    std = select_standard_remuneration(standard_history, month) if (h_ins or p_ins) else None
    h_rates = (
        select_version(list(health_rate_versions), month).rates_for(prefecture) if h_ins else None
    )
    p_rates = select_version(list(pension_rate_versions), month).rates if p_ins else None
    exempt_set = {month_start(m) for m in exempt_months}
    return calculate_monthly_premium(
        month,
        birth_date=birth_date,
        health_standard=std.health_standard if std else None,
        pension_standard=std.pension_standard if std else None,
        health_rates=h_rates,
        pension_rates=p_rates,
        health_insured=h_ins,
        pension_insured=p_ins,
        exempt=month in exempt_set,
    )


def calculate_pay_deduction(
    pay_month: date,
    *,
    timing: str,
    birth_date: date,
    prefecture: str,
    health_qualification: Qualification | None,
    pension_qualification: Qualification | None,
    standard_history: Sequence[StandardRemuneration],
    health_rate_versions: Iterable[HealthInsuranceRateVersion],
    pension_rate_versions: Iterable[PensionRateVersion],
    exempt_months: Collection[date] = (),
    is_final_pay: bool = False,
    deduct_two_months: bool = True,
) -> PayDeduction:
    """支給月の給与で控除する社会保険料を、対象月ごとに計算する（SIR-24〜26）。

    退職時の2か月控除では、各月をその月の標準報酬月額・料率で計算する
    （単純な2倍にしない）。
    """
    quals = [q for q in (health_qualification, pension_qualification) if q is not None]
    plan = plan_deduction(
        pay_month, timing, quals, is_final_pay=is_final_pay, deduct_two_months=deduct_two_months
    )
    h_versions = list(health_rate_versions)
    p_versions = list(pension_rate_versions)
    premiums = tuple(
        calculate_premium_for_month(
            m,
            birth_date=birth_date,
            prefecture=prefecture,
            health_qualification=health_qualification,
            pension_qualification=pension_qualification,
            standard_history=standard_history,
            health_rate_versions=h_versions,
            pension_rate_versions=p_versions,
            exempt_months=exempt_months,
        )
        for m in plan.target_months
    )
    return PayDeduction(plan, premiums)


@dataclass(frozen=True)
class NoticeReconciliation:
    """事業所全体の保険料（納入告知額の試算）と個人別積上げの差異（SIR-27）。

    notice_amount: Σ全額 の1円未満切捨て（事業主が納付する額）
    employer_payable: notice_amount − Σ本人負担
    individual_employer_sum: 個人別の事業主負担の合計
    difference: individual_employer_sum − employer_payable（端数の範囲 0以上1円未満になる）
    """

    notice_amount: Decimal
    employee_sum: Decimal
    employer_payable: Decimal
    individual_employer_sum: Decimal
    difference: Decimal


def reconcile_with_notice(shares: Iterable[PremiumShare]) -> NoticeReconciliation:
    """同じ種類の保険料（例：全員の厚年）を集計し、納入告知額の試算と比べる。"""
    items = list(shares)
    total = sum((s.total for s in items), ZERO)
    employee_sum = sum((s.employee for s in items), ZERO)
    individual = sum((s.employer for s in items), ZERO)
    notice = floor_yen(total)
    payable = notice - employee_sum
    return NoticeReconciliation(notice, employee_sum, payable, individual, individual - payable)


@dataclass(frozen=True)
class TableMismatch:
    """保険料額表との突合で一致しなかった等級。expected_half が None なら額表に行がない。"""

    grade: int
    standard: Decimal
    expected_half: Decimal | None
    calculated_half: Decimal
    calculated_employee: Decimal


def reconcile_premium_table(
    table: GradeTable, rate: Number, expected_half: Mapping[int, Number]
) -> list[TableMismatch]:
    """保険料額表の折半額と全等級の計算結果を突合する（5.3 の取込み、8章 #1）。

    expected_half: 等級番号 → 額表の折半額（銭まで）。計算した折半額と一致しない等級と、
    額表に行がない等級を返す。一致した等級の本人負担は calculated_employee の値になる。
    """
    mismatches = []
    for g in table.grades:
        share = split_premium(g.standard, rate)
        expected = expected_half.get(g.grade)
        if expected is None or to_decimal(expected) != share.half:
            mismatches.append(
                TableMismatch(
                    g.grade,
                    g.standard,
                    None if expected is None else to_decimal(expected),
                    share.half,
                    share.employee,
                )
            )
    return mismatches
