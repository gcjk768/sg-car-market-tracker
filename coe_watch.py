"""Result day COE watcher. The scheduler starts it at coe.push.start and it polls until the
new tender is published, posts the COE section once, then exits.

  python coe_watch.py            wait for today's tender, send once
  python coe_watch.py --dry-run  poll once and print what it would do
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import vault
from db import Database
from pipeline import Pipeline
from scrapers.coe import scrape_coe
from settings import load_config, load_secrets, user_agent
from telegram_bot import TelegramClient

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
    push = cfg["coe"].get("push", {})
    tz = ZoneInfo(cfg["general"].get("timezone", "Asia/Singapore"))
    today = datetime.now(tz).date()
    if "--dry-run" in argv:
        print("published:", published(cfg, today))
        return 0
    stop_h, stop_m = (int(x) for x in str(push.get("stop", "16:30")).split(":"))
    interval = int(push.get("poll_seconds", 120))
    while True:
        now = datetime.now(tz)
        if (now.hour, now.minute) >= (stop_h, stop_m):
            log.warning("COE tender not published by %02d:%02d, giving up", stop_h, stop_m)
            vault.event("⚠️", "coe push", "tender not published before the window closed")
            return 1
        if published(cfg, today):
            break
        time.sleep(interval)
    db = Database(cfg["general"]["db_path"])
    try:
        pipe = Pipeline(cfg, db, today, user_agent(cfg), force=True)
        sections = pipe.build("coe")
        s = load_secrets()
        client = TelegramClient(s["telegram_bot_token"], s["telegram_chat_id"], api_base=s.get("telegram_api_base"),
                                thread_id=s.get("telegram_thread_id"))
        sent = client.send_many(x.html for x in sections)
        vault.event("✅", "coe push sent", f"{sent} messages")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    from main import setup_logging
    cfg = load_config()
    setup_logging(__import__("settings").PROJECT_ROOT / cfg["general"]["log_dir"], 5, 5, filename="coe_watch.log")
    sys.exit(main())
