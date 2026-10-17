"""Collect new news that affects buying or owning a car for the website's News list: LTA's releases feed
and the press feeds in sources.media_news_feeds (The Straits Times). The scheduler runs this a few times a
day. New relevant items go into sent_state key news_items (a JSON list of {"title", "link", "published",
"summary", "source"}, newest first, capped at NEWS_CAP), which website.py shows on the Today page at its
next build. Each feed keeps its own seen links (LTA in lta_news_seen, the others in "news_seen <url>"), so
an item is added once, and a feed read for the first time adds only its newest relevant items
(FIRST_READ_CAP) instead of flooding the list. A feed that fails never stops the others; one that answers
403, 429 or a challenge page is skipped for the rest of the day (news_blocked).

  python news_watch.py            check and store
  python news_watch.py --dry-run  print what would be stored
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, replace
from datetime import date

import httpx

import vault
from db import Database
from scrapers.lta_news import LtaNewsScraper, NewsItem, relevant
from scrapers.official import Blocked
from settings import load_config, user_agent

log = logging.getLogger("news_watch")
SEEN_KEY = "lta_news_seen"
BLOCKED_KEY = "news_blocked"
NEWS_KEY = "news_items"
NEWS_CAP = 30
FIRST_READ_CAP = 5


def feeds(cfg: dict) -> list[tuple[str, str, str]]:
    """(source name, feed url, seen links state key) for every feed: LTA first, then the press."""
    media = cfg["sources"].get("media_news_feeds") or {}
    return [("LTA", cfg["sources"]["lta_news_feed"], SEEN_KEY)] + [(name, url, f"news_seen {url}") for name, url in media.items()]


def new_items(items: list[NewsItem], seen: set[str] | None, keywords: list[str], exclude: list[str] = (),
              first_cap: int = FIRST_READ_CAP) -> tuple[list[NewsItem], set[str]]:
    """(relevant items not seen before, all links now seen). seen None means the feed's first read: every link
    is recorded and only its newest relevant items are new, at most first_cap."""
    links = {i.link for i in items}
    if seen is None:
        return sorted((i for i in items if relevant(i, keywords, exclude)), key=lambda i: i.published, reverse=True)[:first_cap], links
    return [i for i in items if i.link not in seen and relevant(i, keywords, exclude)], seen | links


def merge_news(stored: list[dict], fresh: list[NewsItem], cap: int = NEWS_CAP) -> list[dict]:
    """Every item newest first (fresh before stored on the same day), one entry per link, at most `cap`."""
    rows = sorted([asdict(i) for i in fresh] + stored, key=lambda r: r["published"], reverse=True)
    out, links = [], set()
    for r in rows:
        if r["link"] not in links:
            links.add(r["link"])
            out.append(r)
    return out[:cap]


def is_block(exc: Exception) -> bool:
    """A challenge page, or a 403 or 429 after the fetch gave up: the site does not want us today."""
    cause = exc.__cause__
    return isinstance(exc, Blocked) or (isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code in (403, 429))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    cfg = load_config()
    today = date.today().isoformat()
    keywords, exclude = cfg["news"]["keywords"], cfg["news"].get("exclude", [])
    scraper = LtaNewsScraper(cfg, user_agent(cfg), date.today(), force=True)
    db = Database(cfg["general"]["db_path"])
    read = 0
    try:
        blocked = json.loads(db.get_state(BLOCKED_KEY) or "{}")
        for source, url, seen_key in feeds(cfg):
            if blocked.get(url) == today:
                continue
            try:
                items = [replace(i, source=source) for i in scraper.run(url)]
            except Exception as exc:   # one feed down never stops the others
                if is_block(exc):
                    log.warning("%s news blocked, skipped for today: %s", source, exc)
                    if not dry:
                        blocked[url] = today
                        db.set_state(BLOCKED_KEY, json.dumps(blocked))
                        vault.event("⛔", f"{source} news blocked", "skipped for the rest of the day", url)
                else:
                    log.warning("%s news feed failed: %s", source, exc)
                    if not dry:
                        vault.event("⚠️", f"{source} news feed failed", str(exc)[:200], url)
                continue
            read += 1
            raw = db.get_state(seen_key)
            fresh, seen = new_items(items, set(json.loads(raw)) if raw else None, keywords, exclude, FIRST_READ_CAP)
            if dry:
                print(f"{source}: {len(fresh)} new relevant of {len(items)}:", *[f"\n- {i.published} {i.title}" for i in fresh])
                continue
            if fresh:
                db.set_state(NEWS_KEY, json.dumps(merge_news(json.loads(db.get_state(NEWS_KEY) or "[]"), fresh), ensure_ascii=False))
            if raw is None:
                vault.event("📰", f"{source} news watcher started", f"{len(items)} existing items recorded, {len(fresh)} relevant added")
            else:
                for i in fresh:
                    vault.event("📰", f"{source} news stored", i.title, i.link)
            # the whole current feed first (a feed holds years of items), then older links, so nothing in the feed is ever dropped
            keep = list(dict.fromkeys([i.link for i in items] + sorted(seen)))[:2000]
            db.set_state(seen_key, json.dumps(keep))
    finally:
        scraper.close()
        db.close()
    return 0 if read else 1


if __name__ == "__main__":
    from main import setup_logging
    cfg = load_config()
    setup_logging(__import__("settings").PROJECT_ROOT / cfg["general"]["log_dir"], 5, 5, filename="news_watch.log")
    sys.exit(main())
