"""Polestar Singapore (Wearnes Automotive, the importer): management unit offers on the offers page.

The model pages redirect to interest forms, so no regular list price is published. The offers page lists
"Polestar 2 SRSM Management Unit at $209,999*": ex management units, limited stock, terms apply. These are
real prices a buyer can pay but not the standard new car price of the variant, so the note says so. The page
never mentions COE, so includes_coe stays "unknown". Polestar sells only electric cars.
"""
from __future__ import annotations

import html as htmllib
import re

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Polestar"
URL = "https://wearnes.polestarimporter.com/offers/"

_ROW = re.compile(r"<li>\s*(Polestar\s+(\d+)\s+[^<]*?Management\s+Units?)\s+at\s+\$([\d,]+)", re.I)


def parse(page: str, source_url: str = URL) -> list[OfficialPrice]:
    rows = []
    for variant, num, price in _ROW.findall(page):
        rows.append(OfficialPrice(
            make=BRAND, model=f"Polestar {num}", variant=" ".join(htmllib.unescape(variant).split()),
            price=int(price.replace(",", "")), fuel="ev", source_url=source_url,
            note="Management unit offer, limited units, terms apply. Not the regular new car price"))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    return parse(s.page(URL))
