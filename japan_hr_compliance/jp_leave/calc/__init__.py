"""年次有給休暇の計算エンジン（Frappe に依存しない純粋な Python）。

規則：年次有給休暇の付与・管理の規則（開発時の要件定義 B-3）。
DB には触らず、入力はデータクラスで受け取る。DocType・日次処理からはこのパッケージを呼ぶ。

モジュール
    dates        応答日・月数・個別基準日（LVR-05）
    grant_table  法定の付与表・会社独自テーブル・法定を下回る行の検出（LVR-02・03）
    category     比例付与の判定・年間所定労働日数の区分（LVR-04・07・08）
    attendance   出勤率の算定（LVR-10〜16）
    grant        個別基準日の付与予定（LVR-20）
    advance      前倒し・分割付与の判定（LVR-21〜23）
    ledger       2年時効・先入先出の充当・取消（LVR-30〜34）
    five_day     年5日の取得義務（LVR-50〜53）
"""

# ruff: noqa: F401
from .advance import AdvanceJudgement, GrantInput, GrantKind, RoundJudgement, judge_advance_grants, ten_day_reach_date
from .attendance import (
    AttendanceRateResult,
    DayRecord,
    DayStatus,
    LeaveTreatment,
    attendance_period,
    calc_attendance_rate,
    check_treatment_change,
    meets_eighty_percent,
)
from .category import (
    AnnualDaysBasis,
    ContractPeriod,
    ContractTerms,
    determine_category,
    default_annual_days_from_actual,
    pick_contract_at,
    select_grant_table,
)
from .dates import add_months, add_years, first_base_date, nth_base_date, service_months
from .five_day import (
    LeaveTake,
    ObligationPeriod,
    ObligationSource,
    OverlapMethod,
    TakeKind,
    TakeUnit,
    build_obligation_periods,
    count_taken_days,
    is_obligation_target,
    notice_points,
    obligation_status,
)
from .grant import GrantPlan, PlanStatus, plan_individual_grant
from .grant_table import LEGAL_GRANT_TABLE, GrantTable, GrantTableRow, WorkCategory, find_below_statutory, legal_days
from .ledger import LeaveLedger, TakeDay, expiry_date
