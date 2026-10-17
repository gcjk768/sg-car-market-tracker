"""News from RSS feeds, filtered to what affects buying and owning a car: LTA's own releases feed and the press
feeds in sources.media_news_feeds (The Straits Times). Both are plain RSS 2.0, so one parser reads them."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

from scrapers.base import BaseScraper, ScraperUnavailable
from scrapers.official import BLOCK_MARKERS, Blocked


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    published: str  # ISO date
    summary: str
    source: str = "LTA"


def _text(node: ET.Element, tag: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", node.findtext(tag) or "")
    raw = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), raw)  # the feed writes some quotes as literal “
    return html.unescape(raw).strip()


def _date(raw: str) -> str:
    for fmt in ("%d %b %Y", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return raw


def parse_feed(xml: str) -> list[NewsItem]:
    items = []
    for it in ET.fromstring(xml.strip()).iter("item"):
        link = (it.findtext("plink") or it.findtext("link") or "").strip()
        if link:
            items.append(NewsItem(_text(it, "title"), link, _date(_text(it, "pubDate")), re.sub(r"\s+", " ", _text(it, "description"))))
    return items


def relevant(item: NewsItem, keywords: list[str], exclude: list[str] = ()) -> bool:
    text = f"{item.title} {item.summary}".lower()
    if any(re.search(r"\b" + re.escape(x.lower()), text) for x in exclude):
        return False
    return any(re.search(r"\b" + re.escape(k.lower()) + r"\b", text) for k in keywords)


class LtaNewsScraper(BaseScraper):
    name = "lta_news"

    def parse(self, text: str) -> list[NewsItem]:
        return parse_feed(text)

    def run(self, url: str | None = None) -> list[NewsItem]:
        """Items of one feed, LTA's when no url is given. A challenge page instead of the feed raises Blocked."""
        # the daily page cache would hide a release published after the first check of the day
        self.force = True
        url = url or self.cfg["sources"]["lta_news_feed"]
        text = self.fetch(url)
        if "<rss" not in text[:3000] and any(m in text[:3000].lower() for m in BLOCK_MARKERS):
            raise Blocked(url)
        items = self.parse(text)
        if not items:
            raise ScraperUnavailable(f"lta_news: empty feed {url}")
        return items
