"""日付の計算（応答日・月数・個別基準日）。

年休の規則（開発時の要件定義 B-3、以下「規則」）5.1 に従う。
応答日がない月はその月の末日とする（民法143条2項の考え方）。
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import List


ONE_DAY = timedelta(days=1)


def add_months(d: date, months: int) -> date:
    """``d`` の ``months`` か月後（負なら前）の応答日を返す。

    応答日がない場合（例：8月31日の6か月後）はその月の末日にする。
    """
    index = d.month - 1 + months
    year = d.year + index // 12
    month = index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_years(d: date, years: int) -> date:
    """``d`` の ``years`` 年後の応答日を返す（2月29日は平年なら2月28日）。"""
    return add_months(d, 12 * years)


def full_months_between(start: date, end: date) -> int:
    """``start`` から ``end`` までに満了した月数を返す。

    ``add_months(start, n) <= end`` となる最大の n。``end`` が ``start`` より前なら 0。
    例：2026-04-01 から 2026-10-01 は 6、2026-08-31 から 2027-02-28 は 6。
    """
    if end < start:
        return 0
    n = (end.year - start.year) * 12 + (end.month - start.month)
    while n > 0 and add_months(start, n) > end:
        n -= 1
    return max(n, 0)


def months_covering(start: date, end: date) -> int:
    """``start`` から ``end``（当日を含む）までの期間の月数。1か月未満の端数は切り上げる。

    例：2026-10-01〜2028-03-31 は 18。
    """
    if end < start:
        raise ValueError("end は start 以後の日付にしてください")
    end_exclusive = end + ONE_DAY
    n = full_months_between(start, end_exclusive)
    if add_months(start, n) < end_exclusive:
        n += 1
    return n


def first_base_date(hire_date: date) -> date:
    """初回の個別基準日（雇入れ日の6か月後の応答日）。LVR-05。"""
    return add_months(hire_date, 6)


def nth_base_date(hire_date: date, n: int) -> date:
    """第 n 回（1 始まり）の個別基準日。

    第2回以降は初回基準日の (n-1) 年後の応答日（規則 5.1）。
    例：2027-08-29 入社は初回 2028-02-29、第2回 2029-02-28。

    起点は雇入れ日。定年後再雇用・転換等で勤続を通算する場合も、基準日は雇入れ日（その雇用の
    雇入れ日）から求め、勤続月数だけを勤続通算起算日から求める。再雇用時に従前の基準日を
    引き継ぐべきかは未確定のため、利用者が確認すること。
    """
    if n < 1:
        raise ValueError("n は 1 以上にしてください")
    return add_years(first_base_date(hire_date), n - 1)


def base_dates_until(hire_date: date, until: date) -> List[date]:
    """``until`` 以前に到来する個別基準日の一覧。"""
    result: List[date] = []
    n = 1
    while True:
        d = nth_base_date(hire_date, n)
        if d > until:
            return result
        result.append(d)
        n += 1


def base_date_ordinal(hire_date: date, base_date: date) -> int:
    """``base_date`` が第何回の個別基準日かを返す。個別基準日でなければ ValueError。"""
    first = first_base_date(hire_date)
    if base_date >= first:
        n = full_months_between(first, base_date) // 12 + 1
        for k in (n, n + 1):
            if nth_base_date(hire_date, k) == base_date:
                return k
    raise ValueError(f"{base_date} は雇入れ日 {hire_date} の個別基準日ではありません")


def service_months(service_start: date, base_date: date) -> int:
    """勤続月数。勤続通算起算日（LVR-06）から基準日の前日までの月数（規則 5.1）。

    起算日を初日として算入するため、``full_months_between(service_start, base_date)`` と同じ。
    例：2026-04-01 起算・基準日 2026-10-01 は 6。
    """
    return full_months_between(service_start, base_date)
