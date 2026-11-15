"""2年時効と古い付与分からの充当。LVR-30〜34、規則 2.5・5.4。

付与1回ごとの残日数（JP Annual Leave Grant）と充当（JP Annual Leave Consumption）を
メモリ上で再現する。DB への保存と hrms の台帳行の作成は呼び出し側で行う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Sequence

from .dates import add_years
from .grant_table import Number, to_days


LEGAL_VALIDITY_YEARS = 2
HALF = Decimal("0.5")


class GrantState(str, Enum):
    ACTIVE = "確定"
    EXPIRED = "失効"
    EXTINGUISHED = "消滅"  # 退職による消滅


class InsufficientBalance(ValueError):
    """充当できる残日数が足りない。"""


def expiry_date(grant_date: date, validity_years: int = LEGAL_VALIDITY_YEARS) -> date:
    """失効日（最終使用日）。付与日の ``validity_years`` 年後の応答日の前日（LVR-30）。

    応答日がない場合（2月29日付与で応答年が平年）は、その月の末日を最終使用日とする
    （2028-02-29 付与は 2030-02-28 まで使用可）。
    例：2026-10-01 付与は 2028-09-30（2028-10-01 の日次処理で失効させる）。
    有効期間は法定の2年より短くできない。
    """
    if validity_years < LEGAL_VALIDITY_YEARS:
        raise ValueError("有効期間は法定の2年より短くできません")
    anniversary = add_years(grant_date, validity_years)
    if anniversary.day != grant_date.day:  # 応答日がなく月末に補正された
        return anniversary
    return anniversary - timedelta(days=1)


@dataclass
class Grant:
    """付与1回分（年休付与）。"""

    grant_id: str
    grant_date: date
    days: Decimal
    expiry: date
    remaining: Decimal
    state: GrantState = GrantState.ACTIVE
    lapsed_days: Decimal = Decimal(0)

    def usable_on(self, day: date) -> bool:
        """``day`` の取得に充当できるか（付与日以後・失効日以前・確定状態）。"""
        return self.state == GrantState.ACTIVE and self.grant_date <= day <= self.expiry


@dataclass(frozen=True)
class TakeDay:
    """取得の1日分。``amount`` は 1（1日）または 0.5（半日）。

    計算例の再現のために 0.5 の倍数の日数をまとめて1日付で渡すこともできる。
    """

    day: date
    amount: Decimal = Decimal(1)
    half_day_part: Optional[str] = None  # 午前／午後（LVR-40）

    def __post_init__(self) -> None:
        amount = to_days(self.amount)
        object.__setattr__(self, "amount", amount)
        if amount <= 0 or (amount / HALF) != (amount / HALF).to_integral_value():
            raise ValueError("取得日数は 0.5 日単位の正の数にしてください")


@dataclass
class Consumption:
    """充当1行（年休充当）。1件の申請が複数の付与分にまたがる場合は行を分ける。"""

    application_id: str
    grant_id: str
    day: date
    amount: Decimal
    half_day_part: Optional[str] = None
    cancelled: bool = False


@dataclass(frozen=True)
class LapseEntry:
    """失効・消滅の記録。hrms の器の台帳に負の行として追加する。"""

    grant_id: str
    effective_date: date
    days: Decimal
    reason: GrantState


@dataclass
class CancelResult:
    """取消の結果。``not_restored`` は失効済みの付与分のため戻さなかった充当（担当者に通知）。"""

    restored: List[Consumption] = field(default_factory=list)
    not_restored: List[Consumption] = field(default_factory=list)

    @property
    def needs_notification(self) -> bool:
        return bool(self.not_restored)


class LeaveLedger:
    """1人の従業員の年休の付与・充当・失効を管理する。"""

    def __init__(self, validity_years: int = LEGAL_VALIDITY_YEARS) -> None:
        if validity_years < LEGAL_VALIDITY_YEARS:
            raise ValueError("有効期間は法定の2年より短くできません")
        self.validity_years = validity_years
        self.grants: Dict[str, Grant] = {}
        self.consumptions: List[Consumption] = []
        self.lapses: List[LapseEntry] = []

    # 付与 -----------------------------------------------------------------
    def add_grant(self, grant_id: str, grant_date: date, days: Number) -> Grant:
        """付与を追加する。失効日は付与日から有効期間で決まる。"""
        if grant_id in self.grants:
            raise ValueError(f"付与 {grant_id} は登録済みです")
        d = to_days(days)
        grant = Grant(grant_id, grant_date, d, expiry_date(grant_date, self.validity_years), d)
        self.grants[grant_id] = grant
        return grant

    def _ordered(self) -> List[Grant]:
        return sorted(self.grants.values(), key=lambda g: (g.grant_date, g.grant_id))

    # 充当 -----------------------------------------------------------------
    def consume(self, application_id: str, takes: Sequence[TakeDay]) -> List[Consumption]:
        """取得を古い付与分から充当する（先入先出。LVR-31）。

        日ごとに、その日に使える（付与日以後・失効日以前の）付与分のうち古いものから充当する。
        基準日をまたぐ申請でも、付与日前の日に新しい付与分は使わない。
        全日分を充当できなければ何も変更せず :class:`InsufficientBalance` を送出する。
        """
        remaining = {g.grant_id: g.remaining for g in self.grants.values()}
        rows: List[Consumption] = []
        for take in sorted(takes, key=lambda t: t.day):
            need = take.amount
            for g in self._ordered():
                if need <= 0:
                    break
                if not g.usable_on(take.day) or remaining[g.grant_id] <= 0:
                    continue
                use = min(need, remaining[g.grant_id])
                remaining[g.grant_id] -= use
                need -= use
                rows.append(Consumption(application_id, g.grant_id, take.day, use, take.half_day_part))
            if need > 0:
                raise InsufficientBalance(f"{take.day} の取得に充当できる残日数が {need} 日不足しています")
        for g in self.grants.values():
            g.remaining = remaining[g.grant_id]
        self.consumptions.extend(rows)
        return rows

    def cancel(self, application_id: str, as_of: date) -> CancelResult:
        """取得の取消で充当を戻す（LVR-33）。

        充当先の付与分が ``as_of`` 時点で失効している（失効日が ``as_of`` より前、または失効・消滅済み）
        場合は戻さず、``not_restored`` に入れる（担当者に通知する）。
        """
        result = CancelResult()
        for c in self.consumptions:
            if c.application_id != application_id or c.cancelled:
                continue
            c.cancelled = True
            g = self.grants[c.grant_id]
            if g.state != GrantState.ACTIVE or g.expiry < as_of:
                result.not_restored.append(c)
            else:
                g.remaining += c.amount
                result.restored.append(c)
        return result

    # 失効・消滅 -------------------------------------------------------------
    def process_lapse(self, as_of: date) -> List[LapseEntry]:
        """日次の失効処理。失効日が ``as_of`` より前の付与分を失効させる（LVR-30）。

        残日数が 0 の付与分も状態を「失効」にするが、記録（台帳の負の行）は残日数がある場合だけ作る。
        """
        entries: List[LapseEntry] = []
        for g in self._ordered():
            if g.state == GrantState.ACTIVE and g.expiry < as_of:
                g.state = GrantState.EXPIRED
                g.lapsed_days = g.remaining
                if g.remaining > 0:
                    entries.append(LapseEntry(g.grant_id, g.expiry + timedelta(days=1), g.remaining, GrantState.EXPIRED))
                g.remaining = Decimal(0)
        self.lapses.extend(entries)
        return entries

    def extinguish_on_retirement(self, retirement_date: date) -> List[LapseEntry]:
        """退職日で残日数を「退職による消滅」として記録する（LVR-34）。"""
        self.process_lapse(retirement_date)
        entries: List[LapseEntry] = []
        for g in self._ordered():
            if g.state == GrantState.ACTIVE:
                g.state = GrantState.EXTINGUISHED
                g.lapsed_days = g.remaining
                if g.remaining > 0:
                    entries.append(LapseEntry(g.grant_id, retirement_date, g.remaining, GrantState.EXTINGUISHED))
                g.remaining = Decimal(0)
        self.lapses.extend(entries)
        return entries

    # 照会 -----------------------------------------------------------------
    def remaining(self, grant_id: str) -> Decimal:
        return self.grants[grant_id].remaining

    def balance(self, on: Optional[date] = None) -> Decimal:
        """残日数の合計。``on`` を指定すると、その日に使える付与分だけを合計する。"""
        return sum(
            (g.remaining for g in self.grants.values() if g.state == GrantState.ACTIVE and (on is None or g.usable_on(on))),
            Decimal(0),
        )
