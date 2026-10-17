"""MINI Singapore (Eurokars): mini.com.sg.

No prices on the official site (checked 2026-10-17). The "Variants & Prices" pages (all-electric MINI Cooper and
Countryman, the only ones the sitemap lists) draw their cards by script from a component whose own data says
`"showPricesInTpCards": false` with `"cards": []`, so even the rendered page shows no price. That data file sits under
/content/, which robots.txt disallows, so it is not read directly either.
scrape() therefore returns [] without fetching anything, and Sgcarmart stays the price source for MINI. parse() is kept
for the saved rendered page: it returns [] and raises once a price shows up, the sign to write a real parser.
"""
from __future__ import annotations

import re

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Mini"
URL = "https://www.mini.com.sg/en_SG/home/range/all-electric-mini-cooper/variants-and-prices.html"
PRICE = re.compile(r"S?\$\s?\d{2,3},\d{3}")


def parse(html: str, source_url: str = URL) -> list[OfficialPrice]:
    if PRICE.search(html):
        raise ValueError(f"MINI now shows prices on {source_url}: write the parser")
    return []


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    # The rendered page carries no prices (see the module note), so a daily headless render would buy nothing.
    # To recheck: parse(s.rendered(URL)).
    return []
