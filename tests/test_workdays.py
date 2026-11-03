import unittest
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from repoaegis.checkpoint import parse_pause_name, pause_name
from repoaegis.workdays import (Calendar, load_calendar, next_workday, parse_calendar, pause_deadline,
                                retention_days)

BEIJING = "Asia/Shanghai"


def beijing(year, month, day, hour=10, minute=30):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(BEIJING))


class CalendarTest(unittest.TestCase):
    """2026 年的例子都按国务院通知（国办发明电〔2025〕7号）核对过。"""

    @classmethod
    def setUpClass(cls):
        cls.cn = load_calendar("cn")

    def test_table_has_all_2026_holidays(self):
        # 元旦 3 + 春节 9 + 清明 3 + 劳动节 5 + 端午 3 + 中秋 3 + 国庆 7
        self.assertEqual(len([d for d in self.cn.holidays if d.year == 2026]), 33)
        self.assertEqual(len(self.cn.workdays), 6)

    def test_thursday_keeps_until_friday(self):
        self.assertEqual(pause_deadline(beijing(2026, 10, 29), self.cn, BEIJING),
                         datetime(2026, 10, 30, 15, 59, 59, tzinfo=UTC))

    def test_friday_keeps_until_monday(self):
        self.assertEqual(pause_deadline(beijing(2026, 10, 30), self.cn, BEIJING),
                         datetime(2026, 11, 2, 15, 59, 59, tzinfo=UTC))

    def test_skips_national_day_holiday(self):
        self.assertEqual(next_workday(date(2026, 9, 30), self.cn), date(2026, 10, 8))

    def test_adjusted_saturday_is_a_workday(self):
        self.assertEqual(next_workday(date(2026, 10, 9), self.cn), date(2026, 10, 10))
        self.assertEqual(next_workday(date(2026, 2, 13), self.cn), date(2026, 2, 14))

    def test_skips_spring_festival(self):
        self.assertEqual(next_workday(date(2026, 2, 14), self.cn), date(2026, 2, 24))

    def test_local_date_decides_the_day(self):
        # UTC 还是周四 17:00，北京已经是周五凌晨 1 点：按北京算，下一个工作日是周一
        paused = datetime(2026, 10, 22, 17, 0, tzinfo=UTC)
        self.assertEqual(pause_deadline(paused, self.cn, BEIJING), datetime(2026, 10, 26, 15, 59, 59, tzinfo=UTC))
        self.assertEqual(pause_deadline(paused, self.cn, "UTC"), datetime(2026, 10, 23, 23, 59, 59, tzinfo=UTC))

    def test_unknown_year_falls_back_to_weekends_and_is_reported(self):
        calendar = load_calendar("cn")
        # 2027-01-01 是周五；表里没有 2027 年，只按周末算
        self.assertEqual(next_workday(date(2026, 12, 31), calendar), date(2027, 1, 1))
        self.assertEqual(calendar.missing_years, {2027})

    def test_no_calendar_counts_weekends_only(self):
        calendar = load_calendar("none")
        self.assertEqual(next_workday(date(2026, 9, 30), calendar), date(2026, 10, 1))

    def test_parse_calendar(self):
        calendar = parse_calendar({"years": {"2030": {"holidays": [["2030-01-01", "2030-01-02"]],
                                                       "workdays": ["2030-01-05"]}}})
        self.assertEqual(calendar, Calendar({date(2030, 1, 1), date(2030, 1, 2)}, {date(2030, 1, 5)}, {2030}))


class RetentionTest(unittest.TestCase):
    def test_rounds_up_to_cover_deadline(self):
        now = datetime(2026, 10, 30, 2, 30, tzinfo=UTC)
        self.assertEqual(retention_days(now, datetime(2026, 11, 2, 15, 59, 59, tzinfo=UTC)), 4)
        self.assertEqual(retention_days(now, datetime(2026, 10, 30, 15, 59, 59, tzinfo=UTC)), 1)
        self.assertEqual(retention_days(now, now), 1)


class PauseNameTest(unittest.TestCase):
    def test_round_trip(self):
        deadline = datetime(2026, 10, 30, 15, 59, 59, tzinfo=UTC)
        name = pause_name(8, "0123456789abcdef", deadline)
        self.assertEqual(name, "repoaegis-pause-issue-8-0123456789abcdef-20261030T155959Z")
        self.assertEqual(parse_pause_name(name, 8), ("0123456789abcdef", deadline))

    def test_other_issue_or_other_artifact(self):
        name = pause_name(8, "0123456789abcdef", datetime(2026, 10, 30, tzinfo=UTC))
        self.assertIsNone(parse_pause_name(name, 80))
        self.assertIsNone(parse_pause_name("repoaegis-issue-8-0123456789abcdef", 8))


if __name__ == "__main__":
    unittest.main()
