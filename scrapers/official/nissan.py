"""Nissan Singapore (Tan Chong Motor): the price feed on each model page of nissan.com.sg.

Each model page carries the site's own price feed as JSON in the element id="individualVehiclePriceJSON": a
gradePrice per grade key (LVL001, LVL002) and an Updated_On stamp. Grade names come from the page's
data-grade-data="NAME=LVL001" attributes. A grade with no name on the page (Serena LVL002, both Kicks grades) or no
price (Qashqai Premium) is left out, since its name cannot be paired with certainty. The hero "STARTING PRICE" text
and the grid's universalSsiPrice differ from the feed and are not read. No page mentions COE, so includes_coe stays
"unknown". Jina Reader refuses this domain (its own block), so pages are read with plain requests.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from urllib.parse import urljoin

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Nissan"
LISTING = "https://www.nissan.com.sg/vehicles/new.html"

_FEED = re.compile(r'id="individualVehiclePriceJSON"[^>]*>(.*?)</iframe>', re.S)
_GRADES = re.compile(r'data-grade-data="([^"]*)"')
_MODEL = re.compile(r'"@type":\[?"Product"\]?.*?"model":"([^"]+)"', re.S)
_TITLE = re.compile(r"<title>([^<]*)</title>", re.I)
_LINK = re.compile(r'href="(/vehicles/new/[a-z0-9-]+\.html)"')


def model_urls(listing: str) -> list[str]:
    """Model page URLs linked from the new vehicles page, in page order, so a new model is picked up by itself."""
    return list(dict.fromkeys(urljoin(LISTING, p) for p in _LINK.findall(listing)))


def _model_name(raw: str) -> str:
    """X-TRAIL e-POWER to X-Trail e-POWER, ARIYA EV to Ariya EV. Only all capital words over two letters change."""
    def word(w: str) -> str:
        if not w.isupper() or len(w) <= 2:
            return w
        return "-".join(p.capitalize() for p in w.split("-"))
    return " ".join(word(w) for w in raw.split())


def parse(page: str, source_url: str = "") -> list[OfficialPrice]:
    feed = _FEED.search(page)
    model = _MODEL.search(page)
    if not feed or not model:
        return []
    names: dict[str, str] = {}
    for attr in _GRADES.findall(page):
        for pair in htmllib.unescape(attr).split(","):
            name, _, key = pair.strip().rpartition("=")
            if name.strip() and key:
                names[key.strip()] = name.strip()
    model_name = _model_name(htmllib.unescape(model.group(1)))
    title = _TITLE.search(page)
    fuel = ("hybrid" if "e-power" in model_name.lower()
            else "ev" if model_name.endswith(" EV") or "electric" in (title.group(1).lower() if title else "")
            else "")
    rows = []
    for data in json.loads(feed.group(1).strip()).values():
        updated = data.get("Updated_On", "")[:10].replace(".", "-")
        for key, grade in data.get("default", {}).get("grades", {}).items():
            price = grade.get("gradePrice") or ""
            if key not in names or not price.isdigit() or int(price) <= 0:
                continue
            rows.append(OfficialPrice(
                make=BRAND, model=model_name, variant=names[key], price=int(price), fuel=fuel,
                source_url=source_url, note=f"Nissan price feed updated {updated}" if updated else ""))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    rows = []
    for url in model_urls(s.page(LISTING)):
        rows.extend(parse(s.page(url), url))
    return rows
