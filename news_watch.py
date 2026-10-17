"""Collect new LTA news releases that affect buying or owning a car for the website's News list. The
scheduler runs this a few times a day. New relevant items go newest first into sent_state key
news_items (a JSON list of {"title", "link", "published", "summary"}, capped at NEWS_CAP), which
website.py shows on the Today page at its next build. Links already seen are kept in lta_news_seen,
so an item is added once.

  python news_watch.py            check and store
  python news_watch.py --dry-run  print what would be stored
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from datetime import date

import vault
from db import Database
from scrapers.lta_news import LtaNewsScraper, NewsItem, relevant
from settings import load_config, user_agent

log = logging.getLogger("news_watch")
SEEN_KEY = "lta_news_seen"
NEWS_KEY = "news_items"
NEWS_CAP = 30


def new_items(items: list[NewsItem], seen: set[str] | None, keywords: list[str], exclude: list[str] = ()) -> tuple[list[NewsItem], set[str]]:
    """(relevant items not seen before, all links now seen). seen None means first run: nothing is new."""
    links = {i.link for i in items}
    if seen is None:
        return [], links
    return [i for i in items if i.link not in seen and relevant(i, keywords, exclude)], seen | links


def merge_news(stored: list[dict], fresh: list[NewsItem], cap: int = NEWS_CAP) -> list[dict]:
    """Fresh items first, newest first, then the stored ones, one entry per link, at most `cap`."""
    rows = sorted((asdict(i) for i in fresh), key=lambda r: r["published"], reverse=True) + stored
    out, links = [], set()
    for r in rows:
        if r["link"] not in links:
            links.add(r["link"])
            out.append(r)
    return out[:cap]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = load_config()
    scraper = LtaNewsScraper(cfg, user_agent(cfg), date.today(), force=True)
    try:
        items = scraper.run()
    finally:
        scraper.close()
    keywords, exclude = cfg["news"]["keywords"], cfg["news"].get("exclude", [])
    db = Database(cfg["general"]["db_path"])
    try:
        raw = db.get_state(SEEN_KEY)
        fresh, seen = new_items(items, set(json.loads(raw)) if raw else None, keywords, exclude)
        if "--dry-run" in argv:
            print(f"{len(fresh)} new relevant of {len(items)}:", *[f"\n- {i.published} {i.title}" for i in fresh])
            return 0
        stored = db.get_state(NEWS_KEY)
        if stored is None:
            # First time the site list is kept: start it with the relevant releases already in the feed.
            start = [i for i in items if relevant(i, keywords, exclude)]
            db.set_state(NEWS_KEY, json.dumps(merge_news([], start), ensure_ascii=False))
            vault.event("📰", "LTA news list started", f"{min(len(start), NEWS_CAP)} relevant releases from the feed")
        elif fresh:
            db.set_state(NEWS_KEY, json.dumps(merge_news(json.loads(stored), fresh), ensure_ascii=False))
            for i in fresh:
                vault.event("📰", "LTA news stored", i.title, i.link)
        if raw is None:
            vault.event("📰", "LTA news watcher started", f"{len(items)} existing releases recorded")
        # the whole current feed first (a feed holds years of items), then older links, so nothing in the feed is ever dropped
        keep = list(dict.fromkeys([i.link for i in items] + sorted(seen)))[:2000]
        db.set_state(SEEN_KEY, json.dumps(keep))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    from main import setup_logging
    cfg = load_config()
    setup_logging(__import__("settings").PROJECT_ROOT / cfg["general"]["log_dir"], 5, 5, filename="news_watch.log")
    sys.exit(main())
