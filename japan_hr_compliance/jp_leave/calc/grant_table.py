"""付与テーブル（法定の通常付与・比例付与の表と会社独自テーブル）。LVR-02・03。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union


Number = Union[int, float, str, Decimal]


def to_days(value: Number) -> Decimal:
    """日数を Decimal に揃える（float は文字列経由で変換して誤差を避ける）。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


class WorkCategory(str, Enum):
    """付与の区分（通常／比例付与の週4〜1日／付与対象外）。"""

    NORMAL = "通常"
    WEEK4 = "週4日"
    WEEK3 = "週3日"
    WEEK2 = "週2日"
    WEEK1 = "週1日"
    NONE = "付与対象外"

    @property
    def rank(self) -> int:
        """付与日数の多い順の序列（大きいほど多い）。"""
        return _CATEGORY_RANK[self]


_CATEGORY_RANK = {
    WorkCategory.NONE: 0,
    WorkCategory.WEEK1: 1,
    WorkCategory.WEEK2: 2,
    WorkCategory.WEEK3: 3,
    WorkCategory.WEEK4: 4,
    WorkCategory.NORMAL: 5,
}

#: 法定の表の勤続月数（0.5年〜6.5年以上）
LEGAL_SERVICE_MONTHS: Tuple[int, ...] = (6, 18, 30, 42, 54, 66, 78)

#: 法定の付与日数（規則 2.1・2.2）
LEGAL_DAYS: Dict[WorkCategory, Tuple[int, ...]] = {
    WorkCategory.NORMAL: (10, 11, 12, 14, 16, 18, 20),
    WorkCategory.WEEK4: (7, 8, 9, 10, 12, 13, 15),
    WorkCategory.WEEK3: (5, 6, 6, 8, 9, 10, 11),
    WorkCategory.WEEK2: (3, 4, 4, 5, 6, 6, 7),
    WorkCategory.WEEK1: (1, 2, 2, 2, 3, 3, 3),
}


@dataclass(frozen=True)
class GrantTableRow:
    """付与テーブルの1行：勤続月数（以上）×区分→付与日数。"""

    service_months: int
    category: WorkCategory
    days: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "days", to_days(self.days))
        if self.service_months < 0:
            raise ValueError("勤続月数は 0 以上にしてください")
        if self.days < 0:
            raise ValueError("付与日数は 0 以上にしてください")


@dataclass
class GrantTable:
    """就業規則ごとの付与テーブル（JP Leave Grant Table に対応）。"""

    name: str
    rows: List[GrantTableRow]
    work_rule: Optional[str] = None

    def days_for(self, months: int, category: WorkCategory) -> Decimal:
        """勤続 ``months`` か月・区分 ``category`` の付与日数。

        その区分で勤続月数が ``months`` 以下の行のうち最大の行を使う。該当行がなければ 0。
        """
        best: Optional[GrantTableRow] = None
        for row in self.rows:
            if row.category == category and row.service_months <= months:
                if best is None or row.service_months > best.service_months:
                    best = row
        return best.days if best else Decimal(0)

    def months_of(self, category: WorkCategory) -> List[int]:
        return sorted({r.service_months for r in self.rows if r.category == category})


def build_legal_table(name: str = "法定", work_rule: Optional[str] = None) -> GrantTable:
    """法定の表（通常＋比例付与）から付与テーブルを作る（初期データ）。"""
    rows = [
        GrantTableRow(m, cat, Decimal(d))
        for cat, days in LEGAL_DAYS.items()
        for m, d in zip(LEGAL_SERVICE_MONTHS, days)
    ]
    return GrantTable(name=name, rows=rows, work_rule=work_rule)


LEGAL_GRANT_TABLE = build_legal_table()


def legal_days(months: int, category: WorkCategory) -> Decimal:
    """法定の付与日数。勤続6か月未満・付与対象外は 0。"""
    return LEGAL_GRANT_TABLE.days_for(months, category)


@dataclass(frozen=True)
class BelowStatutoryWarning:
    """法定日数を下回る付与テーブルの箇所（LVR-03）。保存は止めず、警告として記録する。"""

    table: str
    category: WorkCategory
    service_months: int
    table_days: Decimal
    legal_days: Decimal

    @property
    def message(self) -> str:
        return (
            f"付与テーブル「{self.table}」の{self.category.value}・勤続{self.service_months}か月の"
            f"付与日数 {self.table_days} 日が法定の {self.legal_days} 日を下回っています"
        )


def find_below_statutory(table: GrantTable) -> List[BelowStatutoryWarning]:
    """付与テーブルを法定の表と照合し、法定日数を下回る箇所を返す（LVR-03）。

    法定の各段階（6・18…78か月）と、テーブル独自の段階（6か月以上）の各点で、
    テーブルの日数（その時点で適用される行）が法定の日数以上かを確かめる。
    法定の段階に対応する行がない場合も 0 日として警告される。
    """
    warnings: List[BelowStatutoryWarning] = []
    for category in LEGAL_DAYS:
        points = sorted(set(LEGAL_SERVICE_MONTHS) | {m for m in table.months_of(category) if m >= 6})
        for m in points:
            t = table.days_for(m, category)
            legal = legal_days(m, category)
            if t < legal:
                warnings.append(BelowStatutoryWarning(table.name, category, m, t, legal))
    return warnings


def rows_from_mapping(mapping: Dict[WorkCategory, Sequence[Tuple[int, Number]]]) -> List[GrantTableRow]:
    """``{区分: [(勤続月数, 日数), ...]}`` から行の一覧を作る補助関数。"""
    return [GrantTableRow(m, cat, to_days(d)) for cat, pairs in mapping.items() for m, d in pairs]


def iter_categories() -> Iterable[WorkCategory]:
    """付与日数のある区分（通常・週4〜1日）。"""
    return LEGAL_DAYS.keys()
