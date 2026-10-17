"""Subaru Singapore (Motor Image Enterprises): the "from" price per model on the pricing page.

The page lists one h3 per model, "E-Outback  - from $221,800". These are the lowest price of each model, not a full
variant list. Nothing on the page says whether COE is included, so includes_coe stays "unknown".
"""
from __future__ import annotations

import html as htmllib
import re

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Subaru"
URL = "https://www.subaru.asia/sg/en/how-to-buy/pricing-and-finance.php"

_ROW = re.compile(r'<h3 class="title-5[^"]*">\s*(.+?)\s+-\s+from\s+\$([\d,]+)\s*</h3>', re.I)


def parse(page: str, source_url: str = URL) -> list[OfficialPrice]:
    rows = []
    for name, price in _ROW.findall(page):
        variant = " ".join(htmllib.unescape(name).split())
        rows.append(OfficialPrice(
            make=BRAND, model=variant.split()[0], variant=variant, price=int(price.replace(",", "")),
            fuel="hybrid" if "hybrid" in variant.lower() else "",
            source_url=source_url, note="From price, lowest for the model"))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    rows = parse(s.page(URL))
    if not rows:
        raise ValueError("Subaru pricing page: no 'from $' h3 rows found, layout changed?")
    return rows
