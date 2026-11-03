"""按工作日算暂停存档的截止时刻（2026-10-27 定）。

暂停存档保留到"暂停那天之后的下一个工作日"结束，按配置的时区算。工作日 = 不是周末也不是法定假日，
或者是调休上班日。节假日表放在 calendars/ 下，每年按国务院通知更新；表里没有的年份只按周末算，
并记进 missing_years，由调用方在日志里提醒。只用标准库。
"""

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from importlib import resources
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_CALENDAR = "cn"
NO_CALENDAR = "none"  # 只按周末算


@dataclass
class Calendar:
    holidays: set[date] = field(default_factory=set)
    workdays: set[date] = field(default_factory=set)  # 调休上班的周末
    known_years: set[int] = field(default_factory=set)
    missing_years: set[int] = field(default_factory=set)

    def is_workday(self, day: date) -> bool:
        if day.year not in self.known_years:
            self.missing_years.add(day.year)
        if day in self.workdays:
            return True
        return day.weekday() < 5 and day not in self.holidays


def load_calendar(name: str = DEFAULT_CALENDAR) -> Calendar:
    """读 calendars/<name>.json；name 为 none 时返回只按周末算的日历。"""
    if name == NO_CALENDAR:
        return Calendar()
    text = resources.files("repoaegis").joinpath("calendars", f"{name}.json").read_text(encoding="utf-8")
    return parse_calendar(json.loads(text))


def parse_calendar(data: dict) -> Calendar:
    calendar = Calendar()
    for year, entry in data["years"].items():
        calendar.known_years.add(int(year))
        for start, end in entry["holidays"]:
            day, last = date.fromisoformat(start), date.fromisoformat(end)
            while day <= last:
                calendar.holidays.add(day)
                day += timedelta(days=1)
        calendar.workdays.update(date.fromisoformat(day) for day in entry["workdays"])
    return calendar


def next_workday(day: date, calendar: Calendar) -> date:
    """day 之后的第一个工作日，不含 day 本身。"""
    day += timedelta(days=1)
    while not calendar.is_workday(day):
        day += timedelta(days=1)
    return day


def pause_deadline(paused_at: datetime, calendar: Calendar, timezone: str = DEFAULT_TIMEZONE) -> datetime:
    """暂停存档的截止时刻：暂停当天（当地日期）之后的下一个工作日的 23:59:59，返回 UTC。"""
    zone = ZoneInfo(timezone)
    local_day = paused_at.astimezone(zone).date()
    end = datetime.combine(next_workday(local_day, calendar), time(23, 59, 59), tzinfo=zone)
    return end.astimezone(UTC)


def retention_days(now: datetime, deadline: datetime) -> int:
    """产物的 retention-days：整数天，刚好盖过截止时刻，最少 1 天。"""
    return max(1, math.ceil((deadline - now) / timedelta(days=1)))
