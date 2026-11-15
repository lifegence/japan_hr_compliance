"""比例付与の判定と年間所定労働日数による区分。LVR-04・07・08。

雇用契約は B-1 の ``get_contract_at(employee, date)`` が提供する予定であり、
ここでは基準日時点の契約条件を :class:`ContractTerms` として受け取るだけにする。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Mapping, Optional, Sequence

from .grant_table import LEGAL_GRANT_TABLE, GrantTable, WorkCategory, to_days


#: 比例付与の対象となる週所定労働時間の上限（この値未満）
RATIO_WEEKLY_HOURS_LIMIT = Decimal(30)


class AnnualDaysBasis(str, Enum):
    """年間所定労働日数の算出根拠（年休付与に記録する）。"""

    CALENDAR = "カレンダー"  # 会社カレンダー・シフトから基準日以後1年分を算出
    ACTUAL = "実績"  # 勤怠の実績から算出（初回6か月×2、以後前年1年間）
    ESTIMATE = "目安"  # 月の労働日数の目安×12（B-1）


class MissingContractData(ValueError):
    """区分を決めるための契約条件が不足している（付与予定を保留にする）。"""


@dataclass(frozen=True)
class ContractTerms:
    """基準日時点の雇用契約条件（B-1 の雇用契約履歴の写し）。

    Attributes:
        weekly_hours: 週所定労働時間。
        weekly_days: 週所定労働日数（週で定める場合。整数）。シフト制等で定まらなければ None。
        annual_days: 年間所定労働日数（週以外で定める場合）。
        annual_days_basis: ``annual_days`` の根拠（カレンダー／実績／目安）。
        work_rule: 就業規則区分（付与テーブルの選択に使う。LVR-04）。
    """

    weekly_hours: Optional[Decimal]
    weekly_days: Optional[int] = None
    annual_days: Optional[int] = None
    annual_days_basis: AnnualDaysBasis = AnnualDaysBasis.CALENDAR
    work_rule: Optional[str] = None

    def __post_init__(self) -> None:
        if self.weekly_hours is not None:
            object.__setattr__(self, "weekly_hours", to_days(self.weekly_hours))


@dataclass(frozen=True)
class ContractPeriod:
    """雇用契約履歴の1件（適用開始日以後、次の契約まで有効）。"""

    effective_from: date
    terms: ContractTerms


def pick_contract_at(history: Sequence[ContractPeriod], on: date) -> ContractTerms:
    """契約履歴から ``on`` 時点の契約条件を選ぶ（テスト・単体利用向けの補助関数）。

    本番では B-1 の ``get_contract_at`` を使い、その結果を ContractTerms に詰めて渡す。
    """
    candidates = [p for p in history if p.effective_from <= on]
    if not candidates:
        raise MissingContractData(f"{on} 時点の雇用契約がありません")
    return max(candidates, key=lambda p: p.effective_from).terms


def category_from_weekly_days(weekly_days: int) -> WorkCategory:
    """週所定労働日数から区分を決める（週30時間未満の者に使う）。"""
    if weekly_days != int(weekly_days):
        raise ValueError("週所定労働日数は整数で指定してください")
    if weekly_days >= 5:
        return WorkCategory.NORMAL
    if weekly_days <= 0:
        return WorkCategory.NONE
    return {4: WorkCategory.WEEK4, 3: WorkCategory.WEEK3, 2: WorkCategory.WEEK2, 1: WorkCategory.WEEK1}[int(weekly_days)]


def category_from_annual_days(annual_days: int) -> WorkCategory:
    """年間所定労働日数から区分を決める（規則 2.2）。

    217日以上は通常、169〜216日は週4日、121〜168日は週3日、73〜120日は週2日、
    48〜72日は週1日、48日未満は付与対象外。
    """
    if annual_days >= 217:
        return WorkCategory.NORMAL
    if annual_days >= 169:
        return WorkCategory.WEEK4
    if annual_days >= 121:
        return WorkCategory.WEEK3
    if annual_days >= 73:
        return WorkCategory.WEEK2
    if annual_days >= 48:
        return WorkCategory.WEEK1
    return WorkCategory.NONE


def default_annual_days_from_actual(worked_days: int, is_initial: bool) -> int:
    """勤怠の実績から年間所定労働日数の既定値を算出する（LVR-08）。

    初回（雇入れ6か月後）は雇入れから6か月間の労働日数×2、2回目以降は前年1年間の労働日数。
    """
    if worked_days < 0:
        raise ValueError("労働日数は 0 以上にしてください")
    return worked_days * 2 if is_initial else worked_days


@dataclass(frozen=True)
class CategoryDecision:
    """区分の判定結果と根拠（年休付与に記録する）。"""

    category: WorkCategory
    is_ratio: bool
    basis: str
    annual_days_used: Optional[int] = None
    annual_days_basis: Optional[AnnualDaysBasis] = None
    estimate_category: Optional[WorkCategory] = None
    actual_category: Optional[WorkCategory] = None


def determine_category(terms: ContractTerms, actual_annual_days: Optional[int] = None) -> CategoryDecision:
    """基準日時点の契約条件から付与の区分を決める（LVR-07・08）。

    1. 週所定労働時間が30時間以上なら通常付与。
    2. 週所定労働日数が定まっていれば、その日数で区分する（5日以上は通常）。
    3. 定まらなければ年間所定労働日数で区分する。
       - 年間所定労働日数（カレンダー由来）がなく ``actual_annual_days`` があれば実績を使う。
       - 根拠が「目安」の場合は、目安による区分と実績による区分のうち付与日数が多い方を使う
         （実績を下回る付与はしない）。

    Args:
        terms: 基準日時点の契約条件。
        actual_annual_days: 実績から算出した年間所定労働日数
            （:func:`default_annual_days_from_actual` の結果）。

    Raises:
        MissingContractData: 週所定労働時間、または日数の情報がない場合。
    """
    if terms.weekly_hours is None:
        raise MissingContractData("週所定労働時間がありません")
    if terms.weekly_hours >= RATIO_WEEKLY_HOURS_LIMIT:
        return CategoryDecision(WorkCategory.NORMAL, False, "週所定労働時間30時間以上")

    if terms.weekly_days is not None:
        cat = category_from_weekly_days(terms.weekly_days)
        return CategoryDecision(cat, cat != WorkCategory.NORMAL, f"週所定労働日数{terms.weekly_days}日")

    if terms.annual_days is None:
        if actual_annual_days is None:
            raise MissingContractData("週所定労働日数・年間所定労働日数・実績のいずれもありません")
        cat = category_from_annual_days(actual_annual_days)
        return CategoryDecision(
            cat, cat != WorkCategory.NORMAL, f"実績による年間所定労働日数{actual_annual_days}日",
            actual_annual_days, AnnualDaysBasis.ACTUAL, actual_category=cat,
        )

    cat = category_from_annual_days(terms.annual_days)
    if terms.annual_days_basis != AnnualDaysBasis.ESTIMATE or actual_annual_days is None:
        return CategoryDecision(
            cat, cat != WorkCategory.NORMAL,
            f"{terms.annual_days_basis.value}による年間所定労働日数{terms.annual_days}日",
            terms.annual_days, terms.annual_days_basis,
            estimate_category=cat if terms.annual_days_basis == AnnualDaysBasis.ESTIMATE else None,
        )

    actual_cat = category_from_annual_days(actual_annual_days)
    if actual_cat.rank > cat.rank:
        chosen, days, basis = actual_cat, actual_annual_days, AnnualDaysBasis.ACTUAL
    else:
        chosen, days, basis = cat, terms.annual_days, AnnualDaysBasis.ESTIMATE
    return CategoryDecision(
        chosen, chosen != WorkCategory.NORMAL,
        f"目安{terms.annual_days}日（{cat.value}）と実績{actual_annual_days}日（{actual_cat.value}）の多い方",
        days, basis, estimate_category=cat, actual_category=actual_cat,
    )


def select_grant_table(
    terms: ContractTerms, tables: Mapping[str, GrantTable], default: Optional[GrantTable] = None
) -> GrantTable:
    """基準日時点の就業規則区分から付与テーブルを選ぶ（LVR-04）。

    就業規則区分に対応するテーブルがなければ ``default``（省略時は法定の表）。
    """
    if terms.work_rule is not None and terms.work_rule in tables:
        return tables[terms.work_rule]
    return default if default is not None else LEGAL_GRANT_TABLE


__all__ = [
    "AnnualDaysBasis",
    "CategoryDecision",
    "ContractPeriod",
    "ContractTerms",
    "MissingContractData",
    "category_from_annual_days",
    "category_from_weekly_days",
    "default_annual_days_from_actual",
    "determine_category",
    "pick_contract_at",
    "select_grant_table",
]
