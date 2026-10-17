"""Toyota Singapore (Borneo Motors) passenger car models page, read through the headless browser.

The old price list address now redirects to /configure/models, a React widget that draws the prices by script, so
the plain HTML has none. Each model card (data-test="context:car:<id>") carries the model title and one price
(data-test="text:price:primary") next to a monthly instalment. A card names no trim, so variant stays empty. Per
trim prices live on the configurator pages, which are not read here. The page never says whether COE is included.
"""
from __future__ import annotations

import html
import re

from scrapers.official import BLOCK_MARKERS, Blocked, OfficialPrice, OfficialScraper

BRAND = "Toyota"
URL = "https://www.toyota.com.sg/configure/models"

_CARD = re.compile(r'data-test="context:car:\d+"')
_TITLE = re.compile(r'data-test="text:model_title"[^>]*>([^<]+)<')
_PRICE = re.compile(r'data-test="text:price:primary"[^>]*>\s*\$([\d,]+)\s*<')   # the monthly figure has its own tag


def _name(raw: str) -> str:
    """"COROLLA ALTIS" to "Corolla Altis"; mixed case or digit words ("bZ4X", "RAV4") stay as written."""
    return " ".join(w.capitalize() if w.isalpha() and w.isupper() else w for w in html.unescape(raw).split())


def parse(page: str, source_url: str = URL) -> list[OfficialPrice]:
    rows = []
    for card in _CARD.split(page)[1:]:   # one block per model card, so a price never crosses cards
        title, price = _TITLE.search(card), _PRICE.search(card)
        if not (title and price):
            continue
        model = _name(title.group(1))
        rows.append(OfficialPrice(
            make=BRAND, model=model, variant="", price=int(price.group(1).replace(",", "")),
            fuel="hybrid" if "hybrid" in model.lower() else "", source_url=source_url,
            note="One price per model on the models page; trims are priced in the configurator."))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    page = s.rendered(URL, wait_selector='[data-test="text:price:primary"]')
    rows = parse(page)
    if not rows:   # a challenge page or a new layout would otherwise pass as "0 prices"
        if any(m in page[:5000].lower() for m in BLOCK_MARKERS):
            raise Blocked(URL)
        raise ValueError("no priced model cards on the Toyota models page")
    return rows
