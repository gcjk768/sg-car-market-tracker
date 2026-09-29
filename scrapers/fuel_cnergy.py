"""Cnergy pump prices from cnergy.sg. Cnergy (Union Gas) sells well below the big chains at a
handful of stations, with a member price and a public price per grade.

The page could not be opened while this was written, so the parser reads any table or text
block that pairs a grade (92, 95, 98, diesel) with dollar amounts, and treats an amount that
sits next to the word member as the member price.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Optional

from selectolax.parser import HTMLParser

from scrapers.base import BaseScraper, ScraperUnavailable
from scrapers.parse_utils import clean, parse_number, tables, text_of

log = logging.getLogger(__name__)

GRADES = {"92": "92", "95": "95", "98": "98", "diesel": "diesel"}
GRADE_RE = re.compile(r"\b(?:ron\s*)?(92|95|98)\b|\b(diesel)\b", re.I)
PRICE_RE = re.compile(r"\$?\s*(\d\.\d{2})\b")


def _grade_of(text: str) -> Optional[str]:
    m = GRADE_RE.search(text)
    if not m:
        return None
    return (m.group(1) or m.group(2)).lower()


def parse_cnergy_prices(html: str) -> dict[str, dict[str, float]]:
    """{grade: {"public": x, "member": y}} with whichever of the two the page shows."""
    out: dict[str, dict[str, float]] = {}
    # Tables first: a grade column plus member and public columns, or grade rows with one price.
    for headers, rows in tables(html):
        low = [h.lower() for h in headers]
        member_col = next((i for i, h in enumerate(low) if "member" in h and "non" not in h), None)
        public_col = next((i for i, h in enumerate(low) if "public" in h or "non-member" in h or "non member" in h or "retail" in h or "walk" in h), None)
        for row in rows:
            if not row:
                continue
            grade = _grade_of(row[0])
            if not grade:
                continue
            entry = out.setdefault(grade, {})
            if member_col is not None and member_col < len(row):
                v = parse_number(row[member_col])
                if v and 0.5 < v < 8:
                    entry["member"] = v
            if public_col is not None and public_col < len(row):
                v = parse_number(row[public_col])
                if v and 0.5 < v < 8:
                    entry["public"] = v
            if "member" not in entry and "public" not in entry:
                prices = [float(p) for p in PRICE_RE.findall(" ".join(row[1:]))]
                if prices:
                    entry["public"] = prices[0]
                    if len(prices) > 1:
                        entry["member"] = min(prices)
                        entry["public"] = max(prices)
        if out:
            return out
    # Text fallback: scan sentences that mention a grade and a price.
    tree = HTMLParser(html)
    text = text_of(tree.body) if tree.body else clean(html)
    for sentence in re.split(r"\.(?!\d)|\n|\|", text):
        grade = _grade_of(sentence)
        if not grade:
            continue
        prices = [float(p) for p in PRICE_RE.findall(sentence)]
        if not prices:
            continue
        entry = out.setdefault(grade, {})
        if "member" in sentence.lower():
            entry.setdefault("member", min(prices))
            if len(prices) > 1:
                entry.setdefault("public", max(prices))
        else:
            entry.setdefault("public", prices[0])
    return out


class CnergyScraper(BaseScraper):
    name = "cnergy"

    def parse(self, html: str) -> list[dict[str, Any]]:
        prices = parse_cnergy_prices(html)
        if not prices:
            raise ScraperUnavailable("no Cnergy prices found on the page")
        return [prices]

    def run(self) -> list[dict[str, Any]]:
        url = self.cfg["sources"]["cnergy"]
        try:
            return self.parse(self.fetch(url))
        except ScraperUnavailable:
            log.info("cnergy static page had no prices, trying a rendered fetch")
            return self.parse(self.fetch_rendered(url))


def scrape_cnergy(cfg: dict[str, Any], user_agent: str, run_date: date, force: bool = False) -> dict[str, dict[str, float]]:
    scraper = CnergyScraper(cfg, user_agent, run_date, force)
    try:
        return scraper.run()[0]
    except Exception as exc:
        log.warning("cnergy prices unavailable: %s", exc)
        return {}
    finally:
        scraper.close()
