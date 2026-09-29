from datetime import datetime
from zoneinfo import ZoneInfo

from scheduler import next_run

SG = ZoneInfo("Asia/Singapore")


def test_next_run_today_or_tomorrow():
    assert next_run(datetime(2026, 9, 29, 7, 30, tzinfo=SG), "08:00") == datetime(2026, 9, 29, 8, 0, tzinfo=SG)
    assert next_run(datetime(2026, 9, 29, 8, 0, tzinfo=SG), "08:00") == datetime(2026, 9, 30, 8, 0, tzinfo=SG)
    assert next_run(datetime(2026, 12, 31, 23, 59, tzinfo=SG), "08:00") == datetime(2027, 1, 1, 8, 0, tzinfo=SG)
