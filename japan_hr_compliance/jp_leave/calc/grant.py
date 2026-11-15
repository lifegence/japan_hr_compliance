"""個別基準日の付与予定の算定（基準日・勤続月数・区分・日数・出勤率の組み合わせ）。LVR-04〜08・14・20。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Mapping, Optional

from .attendance import AttendanceRateResult
from .category import CategoryDecision, ContractTerms, MissingContractData, determine_category, select_grant_table
from .dates import base_date_ordinal, service_months
from .five_day import is_obligation_target
from .grant_table import GrantTable, WorkCategory


class PlanStatus(str, Enum):
    """付与予定の状態（年休付与の状態：予定／保留）。"""

    PLANNED = "予定"  # 基準日に自動で確定する
    PENDING = "保留"  # データ不足・勤怠未確定で確認待ち
    NO_GRANT = "付与なし"  # 出勤率8割未満（勤続の計算は継続）


@dataclass
class GrantPlan:
    """付与予定（JP Annual Leave Grant の予定状態に写す項目）。"""

    base_date: date
    ordinal: int
    service_months: int
    status: PlanStatus
    days: Decimal = Decimal(0)
    category: Optional[WorkCategory] = None
    category_decision: Optional[CategoryDecision] = None
    table_name: Optional[str] = None
    attendance: Optional[AttendanceRateResult] = None
    reasons: List[str] = field(default_factory=list)

    @property
    def is_five_day_target(self) -> bool:
        return self.status == PlanStatus.PLANNED and is_obligation_target(self.days)


def plan_individual_grant(
    hire_date: date,
    base_date: date,
    contract: Optional[ContractTerms],
    attendance: Optional[AttendanceRateResult],
    tables: Optional[Mapping[str, GrantTable]] = None,
    service_start: Optional[date] = None,
    actual_annual_days: Optional[int] = None,
    service_start_confirmed: bool = True,
) -> GrantPlan:
    """個別基準日の付与予定を作る（LVR-20）。

    Args:
        hire_date: 雇入れ日（個別基準日の起点。再雇用時に従前の基準日を引き継ぐかは未確定のため利用者が確認すること）。
        base_date: 今回の基準日。
        contract: 基準日時点の契約条件（B-1 の ``get_contract_at`` の結果）。None なら保留。
        attendance: 今回の算定期間の出勤率の算定明細。None なら保留。
        tables: 就業規則区分→付与テーブル（省略時は法定の表）。
        service_start: 勤続通算起算日（省略時は雇入れ日。LVR-06）。
        actual_annual_days: 実績による年間所定労働日数（LVR-08）。
        service_start_confirmed: 勤続通算が確定しているか。False なら保留。

    Returns:
        付与予定。出勤率8割未満は「付与なし」として記録する（勤続の計算は継続）。
    """
    ordinal = base_date_ordinal(hire_date, base_date)
    months = service_months(service_start or hire_date, base_date)
    plan = GrantPlan(base_date=base_date, ordinal=ordinal, service_months=months, status=PlanStatus.PENDING)

    if not service_start_confirmed:
        plan.reasons.append("勤続通算の起算日が未確定です")
    if contract is None:
        plan.reasons.append("基準日時点の雇用契約がありません")
    else:
        try:
            decision = determine_category(contract, actual_annual_days)
        except MissingContractData as e:
            plan.reasons.append(str(e))
        else:
            table = select_grant_table(contract, tables or {})
            plan.category_decision = decision
            plan.category = decision.category
            plan.table_name = table.name
            plan.days = table.days_for(months, decision.category)

    plan.attendance = attendance
    if attendance is None:
        plan.reasons.append("出勤率の算定がありません")
    elif attendance.is_pending:
        plan.reasons.append(f"勤怠が未確定の日が {len(attendance.unrecorded_days)} 日あります")
    elif attendance.eligible is None:
        plan.reasons.append("全労働日が 0 日のため出勤率を判定できません")

    if plan.reasons:
        plan.status = PlanStatus.PENDING
        return plan
    if not attendance.eligible:  # type: ignore[union-attr]
        plan.status = PlanStatus.NO_GRANT
        plan.days = Decimal(0)
        plan.reasons.append("出勤率が8割未満のため付与しません（勤続期間の計算は継続）")
        return plan
    plan.status = PlanStatus.PLANNED
    return plan
