"""Leapmotor Singapore (Cycle & Carriage, the distributor): the "From $X incl. COE" bar on each model page.

The model pages are a Vue app, so they are read through the headless browser. Each page shows one starting
price for the model with COE included. The price list the site links (a short link to a dated PDF) carries its
prices as pictures, with no text layer, so it is not read. Every Leapmotor sold here is electric.
"""
from __future__ import annotations

import re

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Leapmotor"
BASE = "https://www.cyclecarriage.com"
START = BASE + "/sg/leapmotor/b10"

_MODEL_LINK = re.compile(r'href="(?:https://www\.cyclecarriage\.com)?(/sg/leapmotor/([a-z]\d{1,3}))"')
_BAR = re.compile(r'class="priceBar-detail">(.*?)</section>', re.S)
_PRICE = re.compile(r"<em>\s*\$([\d,]+)\s*</em>")


def models(page: str) -> dict[str, str]:
    """{model page url: model name} from the menu links, e.g. /sg/leapmotor/c10 gives C10."""
    return {BASE + path: slug.upper() for path, slug in _MODEL_LINK.findall(page)}


def parse(pages: dict[str, str]) -> list[OfficialPrice]:
    """Rows from {model page url: rendered html}. A page without the price bar is left out."""
    rows = []
    for url, page in pages.items():
        bar = _BAR.search(page)
        price = _PRICE.search(bar.group(1)) if bar else None
        if not price:
            continue
        coe = "with" if re.search(r"incl\.?\s*COE", bar.group(1), re.I) else "unknown"
        rows.append(OfficialPrice(make=BRAND, model=url.rsplit("/", 1)[1].upper(), variant="",
                                  price=int(price.group(1).replace(",", "")), includes_coe=coe, fuel="ev",
                                  source_url=url, note="Price from: the cheapest variant of the model"))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    first = s.rendered(START)
    return parse({url: first if url == START else s.rendered(url) for url in models(first)})
