"""金額・年月の共通処理（社会保険・雇用保険の計算エンジン内部用）。

このモジュールは Frappe に依存しない。年月は ``datetime.date`` の 1 日で表す。
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_FLOOR, Decimal

Number = Decimal | int | str

ZERO = Decimal("0")
ONE = Decimal("1")
HALF = Decimal("0.5")
THOUSAND = Decimal("1000")


def to_decimal(value: Number) -> Decimal:
    """金額・料率を Decimal に変換する。

    float は二進誤差を持ち込むため受け付けない（文字列か Decimal で渡す）。
    """
    if isinstance(value, bool):
        raise TypeError("bool は金額として扱えません")
    if isinstance(value, float):
        raise TypeError("float は使えません。Decimal または文字列で渡してください")
    if isinstance(value, Decimal):
        return value
    return Decimal(value)


def floor_yen(amount: Number) -> Decimal:
    """1円未満を切り捨てる（負数は 0 方向ではなく小さい方へ）。"""
    return to_decimal(amount).to_integral_value(rounding=ROUND_FLOOR)


def floor_thousand(amount: Number) -> Decimal:
    """1,000円未満を切り捨てる（標準賞与額・年度更新の賃金総額）。"""
    value = to_decimal(amount)
    if value < 0:
        raise ValueError("負の金額は扱えません")
    return (value / THOUSAND).to_integral_value(rounding=ROUND_DOWN) * THOUSAND


def month_start(d: date) -> date:
    """日付をその月の 1 日（年月の表現）に正規化する。"""
    return d.replace(day=1)


def month_end(d: date) -> date:
    """その月の末日を返す。"""
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, months: int) -> date:
    """月を加減する。応当日がない場合はその月の末日に丸める。"""
    index = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(index, 12)
    day = min(d.day, calendar.monthrange(year, month0 + 1)[1])
    return date(year, month0 + 1, day)


def iter_months(first: date, last: date) -> Iterator[date]:
    """first の月から last の月まで（両端を含む）の年月を順に返す。"""
    current = month_start(first)
    stop = month_start(last)
    while current <= stop:
        yield current
        current = add_months(current, 1)


def fiscal_year(d: date) -> int:
    """年度（4月1日〜3月31日）を返す。2027年3月31日 → 2026。"""
    return d.year if d.month >= 4 else d.year - 1


def next_day(d: date) -> date:
    """翌日を返す。"""
    return d + timedelta(days=1)


def previous_day(d: date) -> date:
    """前日を返す。"""
    return d - timedelta(days=1)
