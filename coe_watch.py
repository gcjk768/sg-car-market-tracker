"""Result day COE watcher. The scheduler starts it at coe.results_watch.start and it polls until the
new tender is published, stores it, rebuilds the website data once so the site shows the result the
same afternoon, then exits.

  python coe_watch.py            wait for today's tender, update the site once
  python coe_watch.py --dry-run  poll once and print whether it is out
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import vault
import website
from db import Database
from pipeline import Pipeline
from scrapers.coe import ensure_schedule, scrape_coe
from settings import load_config, user_agent

log = logging.getLogger("coe_watch")


def published(cfg: dict, today: date) -> bool:
    """True when a source already lists the tender dated today."""
    try:
        results = scrape_coe(cfg, user_agent(cfg), today, force=True)
    except Exception as exc:
        log.info("COE not readable yet: %s", exc)
        return False
    return bool(results) and max(r.tender_date for r in results) >= today


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = load_config()
    watch = cfg["coe"].get("results_watch", {})
    tz = ZoneInfo(cfg["general"].get("timezone", "Asia/Singapore"))
    today = datetime.now(tz).date()
    ensure_schedule(cfg, user_agent(cfg), today)
    if "--dry-run" in argv:
        print("published:", published(cfg, today))
        return 0
    stop_h, stop_m = (int(x) for x in str(watch.get("stop", "16:30")).split(":"))
    interval = int(watch.get("poll_seconds", 120))
    while True:
        now = datetime.now(tz)
        if (now.hour, now.minute) >= (stop_h, stop_m):
            log.warning("COE tender not published by %02d:%02d, giving up", stop_h, stop_m)
            vault.event("⚠️", "coe results watch", "tender not published before the window closed")
            return 1
        if published(cfg, today):
            break
        time.sleep(interval)
    db = Database(cfg["general"]["db_path"])
    try:
        Pipeline(cfg, db, today, user_agent(cfg), force=True).run("coe")
        out = website.publish(cfg, db, today, user_agent(cfg))
        vault.event("🎫", "coe results on the site" if out else "coe results stored, site data not written",
                    f"tender {today.isoformat()}")
    finally:
        db.close()
    return 0 if out else 2


if __name__ == "__main__":
    from main import setup_logging
    cfg = load_config()
    setup_logging(__import__("settings").PROJECT_ROOT / cfg["general"]["log_dir"], 5, 5, filename="coe_watch.log")
    sys.exit(main())
