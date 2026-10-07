"""Post new LTA news releases that affect buying or owning a car, once each. The scheduler runs this a few
times a day. The first run only records what is already out, so the topic is not flooded.

  python news_watch.py            check and send
  python news_watch.py --dry-run  print what would be sent
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime

import vault
from db import Database
from report import news_section
from scrapers.lta_news import LtaNewsScraper, NewsItem, relevant
from settings import load_config, load_secrets, user_agent
from telegram_bot import TelegramClient

log = logging.getLogger("news_watch")
SEEN_KEY = "lta_news_seen"


def new_items(items: list[NewsItem], seen: set[str] | None, keywords: list[str], exclude: list[str] = ()) -> tuple[list[NewsItem], set[str]]:
    """(relevant items not seen before, all links now seen). seen None means first run: report nothing."""
    links = {i.link for i in items}
    if seen is None:
        return [], links
    return [i for i in items if i.link not in seen and relevant(i, keywords, exclude)], seen | links


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = load_config()
    scraper = LtaNewsScraper(cfg, user_agent(cfg), date.today(), force=True)
    try:
        items = scraper.run()
    finally:
        scraper.close()
    db = Database(cfg["general"]["db_path"])
    try:
        raw = db.get_state(SEEN_KEY)
        fresh, seen = new_items(items, set(json.loads(raw)) if raw else None, cfg["news"]["keywords"], cfg["news"].get("exclude", []))
        if "--dry-run" in argv:
            print(f"{len(fresh)} new relevant of {len(items)}:", *[f"\n- {i.published} {i.title}" for i in fresh])
            return 0
        if fresh:
            s = load_secrets()
            client = TelegramClient(s["telegram_bot_token"], s["telegram_chat_id"], api_base=s.get("telegram_api_base"),
                                    thread_id=s.get("telegram_thread_id"))
            client.send_many([news_section(fresh).html])
            for i in fresh:
                vault.event("📰", "LTA news sent", i.title, i.link)
        elif raw is None:
            vault.event("📰", "LTA news watcher started", f"{len(items)} existing releases recorded")
        # the whole current feed first (a feed holds years of items), then older links, so nothing in the feed is ever dropped
        keep = list(dict.fromkeys([i.link for i in items] + sorted(seen)))[:2000]
        db.set_state(SEEN_KEY, json.dumps(keep))  # only after a successful send, so a failure is retried
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    from main import setup_logging
    cfg = load_config()
    setup_logging(__import__("settings").PROJECT_ROOT / cfg["general"]["log_dir"], 5, 5, filename="news_watch.log")
    sys.exit(main())
