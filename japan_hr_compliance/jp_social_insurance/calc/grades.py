"""標準報酬月額の等級表と当てはめ（SIR-03、SIR-45）。

健保・厚年は別の表として扱う。等級数は固定しない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._util import Number, to_decimal
from .versions import CONFIRMED

HEALTH = "health"
PENSION = "pension"


@dataclass(frozen=True)
class Grade:
    """等級1行。報酬月額が lower 以上 upper 未満の者に standard を当てはめる。

    最上位等級は upper=None（上限なし）。
    """

    grade: int
    standard: Decimal
    lower: Decimal
    upper: Decimal | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "standard", to_decimal(self.standard))
        object.__setattr__(self, "lower", to_decimal(self.lower))
        if self.upper is not None:
            object.__setattr__(self, "upper", to_decimal(self.upper))

    def contains(self, remuneration: Decimal) -> bool:
        return self.lower <= remuneration and (self.upper is None or remuneration < self.upper)


@dataclass(frozen=True)
class GradeTable:
    """等級表の版（JP Standard Remuneration Table）。kind は HEALTH か PENSION。"""

    kind: str
    effective_from: date
    grades: Sequence[Grade]
    status: str = CONFIRMED

    def __post_init__(self) -> None:
        if self.kind not in (HEALTH, PENSION):
            raise ValueError(f"等級表の種別が不正です: {self.kind}")
        ordered = tuple(sorted(self.grades, key=lambda g: g.grade))
        if len(ordered) < 2:
            raise ValueError("等級表には2等級以上が必要です")
        for prev, cur in zip(ordered, ordered[1:]):
            if cur.grade != prev.grade + 1:
                raise ValueError(f"等級番号が連続していません: {prev.grade} → {cur.grade}")
            if prev.upper is None or prev.upper != cur.lower:
                raise ValueError(f"{prev.grade}等級の上限と{cur.grade}等級の下限が一致しません")
            if cur.standard <= prev.standard:
                raise ValueError("標準報酬月額が等級順に増加していません")
        if ordered[-1].upper is not None:
            raise ValueError("最上位等級の上限は None（上限なし）にしてください")
        object.__setattr__(self, "grades", ordered)

    @property
    def bottom(self) -> Grade:
        return self.grades[0]

    @property
    def top(self) -> Grade:
        return self.grades[-1]

    def lookup(self, remuneration: Number) -> Grade:
        """報酬月額を等級に当てはめる。最下位の下限未満は最下位等級とする。"""
        value = to_decimal(remuneration)
        if value < self.bottom.lower:
            return self.bottom
        for g in self.grades:
            if g.contains(value):
                return g
        return self.top  # 到達しない（最上位は上限なし）

    def by_standard(self, standard: Number) -> Grade:
        """標準報酬月額の金額から等級を引く（履歴は金額で保持。5.2）。"""
        value = to_decimal(standard)
        for g in self.grades:
            if g.standard == value:
                return g
        raise LookupError(f"標準報酬月額 {value} はこの等級表にありません")

    def by_number(self, grade: int) -> Grade:
        return self.grades[grade - self.bottom.grade]

    @property
    def upper_special_threshold(self) -> Decimal:
        """上限の特例の境界値（報酬月額）。

        最上位等級に、1つ下の等級の「標準報酬月額から上限まで」の幅を足した額。
        協会けんぽ・厚年の現行表では 1,415,000円・665,000円になる（2.6）。
        この導出方法は公表資料に明記されたものではなく未確定のため、利用者が公表されている等級表で確認すること。
        """
        second = self.grades[-2]
        assert second.upper is not None
        return self.top.standard + (second.upper - second.standard)

    @property
    def lower_special_threshold(self) -> Decimal:
        """下限の特例の境界値（報酬月額）。

        最下位等級から、1つ上の等級の「下限から標準報酬月額まで」の幅を引いた額。
        現行表では健保 53,000円・厚年 83,000円になる。導出方法は upper と同じく推定。
        """
        second = self.grades[1]
        return self.bottom.standard - (second.standard - second.lower)
