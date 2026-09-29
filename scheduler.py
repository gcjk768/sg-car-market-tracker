"""Container entry point. Runs the daily report at the configured local time and, if asked,
the Telegram command listener in the same container. No cron needed on the NAS.

Environment:
  RUN_ON_START=1   run a report immediately when the container starts (good for a first test)
  RUN_LISTENER=1   also run bot_listener.py so /run, /coe and /filters work
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import main as report_main
from settings import load_config

log = logging.getLogger("scheduler")


def next_run(now: datetime, schedule_time: str) -> datetime:
    """Next occurrence of HH:MM in the same time zone as `now`, strictly after `now`."""
    hour, minute = (int(x) for x in schedule_time.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def run_report() -> None:
    try:
        code = report_main.main([])
        log.info("report run finished with exit code %s", code)
    except Exception:
        log.exception("report run crashed")


def start_listener() -> None:
    import bot_listener

    thread = threading.Thread(target=bot_listener.main, name="bot-listener", daemon=True)
    thread.start()
    log.info("bot listener started")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    tz = ZoneInfo(cfg["general"].get("timezone", "Asia/Singapore"))
    schedule_time = str(cfg["general"].get("schedule_time", "08:00"))
    if os.getenv("RUN_LISTENER") == "1":
        start_listener()
    if os.getenv("RUN_ON_START") == "1":
        run_report()
    while True:
        now = datetime.now(tz)
        target = next_run(now, schedule_time)
        wait = (target - now).total_seconds()
        log.info("next report at %s (%.0f minutes)", target.isoformat(timespec="minutes"), wait / 60)
        time.sleep(wait)
        run_report()


if __name__ == "__main__":
    sys.exit(main())
