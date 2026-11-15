"""テスト用の版データ。

ここの料率・等級表・上限はすべて「例示値」である（計算規則 v1.0 の 5.4・6章、および
現行の等級表の構造を写したもの）。実際の値ではないため、運用では公表資料に基づく値をマスタに登録すること。
エンジン本体には値を持たせず、テストでのみ使う。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from ..grades import HEALTH, PENSION, Grade, GradeTable
from ..versions import (
    DRAFT,
    BonusCapVersion,
    EmploymentInsuranceRateVersion,
    HealthInsuranceRateVersion,
    HealthRates,
    PensionRates,
    PensionRateVersion,
)

TOKYO = "東京都"

# 例示値：令和7年度 東京（3.「正しくは 9.91%／1.59%」）、2025年3月分から
HEALTH_2025_03 = HealthInsuranceRateVersion(
    date(2025, 3, 1), {TOKYO: HealthRates(D("0.0991"), D("0.0159"))}, name="R7"
)
# 例示値：5.4 の 2026年度 東京（健保 9.85%・介護 1.62%）、2026年3月分から。支援金なし
HEALTH_2026_03 = HealthInsuranceRateVersion(
    date(2026, 3, 1), {TOKYO: HealthRates(D("0.0985"), D("0.0162"))}, name="R8"
)
# 例示値：支援金 0.23% は 2026年4月分から
HEALTH_2026_04 = HealthInsuranceRateVersion(
    date(2026, 4, 1), {TOKYO: HealthRates(D("0.0985"), D("0.0162"), D("0.0023"))}, name="R8-support"
)
# 下書きの版（計算に使われないことの確認用。値は架空）
HEALTH_2026_05_DRAFT = HealthInsuranceRateVersion(
    date(2026, 5, 1), {TOKYO: HealthRates(D("0.1500"), D("0.0200"), D("0.0100"))}, status=DRAFT
)
HEALTH_VERSIONS = [HEALTH_2025_03, HEALTH_2026_03, HEALTH_2026_04, HEALTH_2026_05_DRAFT]

# 例示値：厚年 18.3%、拠出金 0.36%
PENSION_VERSION = PensionRateVersion(date(2017, 9, 1), PensionRates(D("0.183"), D("0.0036")))
PENSION_VERSIONS = [PENSION_VERSION]

RATES_2026_04 = HEALTH_2026_04.rates_for(TOKYO)
PENSION_RATES = PENSION_VERSION.rates

# 例示値：賞与の上限（健保 年度累計 573万円、厚年 月150万円）
BONUS_CAPS = BonusCapVersion(date(2016, 4, 1), D("5730000"), D("1500000"))

# 例示値：雇用保険 一般の事業。2025年度 労働者 5.5/1000・事業主 9/1000、
# 2026年度 労働者 5/1000（5.4）・事業主 8.5/1000（事業主負担は仮の値）
EI_GENERAL = "general"
EI_2025 = EmploymentInsuranceRateVersion(date(2025, 4, 1), EI_GENERAL, D("5.5"), D("9"))
EI_2026 = EmploymentInsuranceRateVersion(date(2026, 4, 1), EI_GENERAL, D("5"), D("8.5"))
EI_VERSIONS = [EI_2025, EI_2026]

# 例示値：健康保険の等級表（50等級、58,000円〜1,390,000円）。(標準報酬月額, 下限)
_HEALTH_ROWS = [
    (58000, 0), (68000, 63000), (78000, 73000), (88000, 83000), (98000, 93000),
    (104000, 101000), (110000, 107000), (118000, 114000), (126000, 122000), (134000, 130000),
    (142000, 138000), (150000, 146000), (160000, 155000), (170000, 165000), (180000, 175000),
    (190000, 185000), (200000, 195000), (220000, 210000), (240000, 230000), (260000, 250000),
    (280000, 270000), (300000, 290000), (320000, 310000), (340000, 330000), (360000, 350000),
    (380000, 370000), (410000, 395000), (440000, 425000), (470000, 455000), (500000, 485000),
    (530000, 515000), (560000, 545000), (590000, 575000), (620000, 605000), (650000, 635000),
    (680000, 665000), (710000, 695000), (750000, 730000), (790000, 770000), (830000, 810000),
    (880000, 855000), (930000, 905000), (980000, 955000), (1030000, 1005000), (1090000, 1055000),
    (1150000, 1115000), (1210000, 1175000), (1270000, 1235000), (1330000, 1295000),
    (1390000, 1355000),
]


def _build(kind: str, rows, effective_from: date) -> GradeTable:
    grades = []
    for i, (standard, lower) in enumerate(rows):
        upper = rows[i + 1][1] if i + 1 < len(rows) else None
        grades.append(Grade(i + 1, D(standard), D(lower), None if upper is None else D(upper)))
    return GradeTable(kind, effective_from, grades)


HEALTH_TABLE = _build(HEALTH, _HEALTH_ROWS, date(2020, 9, 1))

# 例示値：厚生年金の等級表（32等級、88,000円〜650,000円）。健保の4〜35等級に相当し、
# 1等級の下限は0、32等級は上限なし
_PENSION_ROWS = [(88000, 0)] + _HEALTH_ROWS[4:35]
PENSION_TABLE = _build(PENSION, _PENSION_ROWS, date(2020, 9, 1))
