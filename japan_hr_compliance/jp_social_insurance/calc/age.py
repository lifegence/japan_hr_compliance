"""年齢到達の判定（SIR-13、2.4、6.2）。

年齢は誕生日の前日に加算される（年齢計算ニ関スル法律）。到達月は誕生日の前日の属する月。
1日生まれは到達日が前月末日になり、到達月が1か月早くなる。
75歳の健保喪失のみ誕生日当日で判定する（2.4）。
"""

from __future__ import annotations

from datetime import date

from ._util import month_start, previous_day

CARE_START_AGE = 40
CARE_END_AGE = 65
PENSION_END_AGE = 70
HEALTH_END_AGE = 75


def birthday_at_age(birth_date: date, age: int) -> date:
    """age 歳の誕生日。2月29日生まれで平年の場合は3月1日とみなす。"""
    year = birth_date.year + age
    try:
        return birth_date.replace(year=year)
    except ValueError:
        return date(year, 3, 1)


def attainment_date(birth_date: date, age: int) -> date:
    """age 歳に到達する日（誕生日の前日）。"""
    return previous_day(birthday_at_age(birth_date, age))


def attainment_month(birth_date: date, age: int) -> date:
    """age 歳の到達月（誕生日の前日の属する月）。"""
    return month_start(attainment_date(birth_date, age))


def care_insurance_applies(birth_date: date, target_month: date) -> bool:
    """対象月が介護保険第2号被保険者の保険料の対象か。

    40歳の到達月分から、65歳の到達月の前月分まで。
    """
    month = month_start(target_month)
    return attainment_month(birth_date, CARE_START_AGE) <= month < attainment_month(
        birth_date, CARE_END_AGE
    )


def pension_loss_date_by_age(birth_date: date) -> date:
    """70歳到達による厚年の資格喪失日（誕生日の前日）。"""
    return attainment_date(birth_date, PENSION_END_AGE)


def health_loss_date_by_age(birth_date: date) -> date:
    """75歳到達による健保の資格喪失日（誕生日当日。後期高齢者医療へ移行）。"""
    return birthday_at_age(birth_date, HEALTH_END_AGE)


def pension_applies_by_age(birth_date: date, target_month: date) -> bool:
    """対象月が年齢上厚年（と拠出金）の保険料の対象か。70歳の到達月の前月分まで。"""
    return month_start(target_month) < month_start(pension_loss_date_by_age(birth_date))


def health_applies_by_age(birth_date: date, target_month: date) -> bool:
    """対象月が年齢上健保（と介護・支援金）の保険料の対象か。75歳の誕生月の前月分まで。"""
    return month_start(target_month) < month_start(health_loss_date_by_age(birth_date))
