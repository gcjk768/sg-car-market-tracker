"""Suzuki: suzukicar.com.sg, run by Champion Motors, the official Singapore distributor.

Each model page prints one figure in plain HTML: `<h1 class="price-from">Price from:</h1>` then
`<h1 class="price-usd">$169,888</h1>`. There are no per variant prices, so each model is one row with an empty
variant. The /price-list page draws its cards by script, so the model pages are read instead.

The model list comes from the site's menu (home page), where the models sit under tabs "HYBRID",
"ELECTRIC VEHICLE (EV)" and "OFF-ROAD 4X4". The tab gives the fuel for hybrids and EVs. The pages never mention
COE, so includes_coe stays "unknown".
"""
from __future__ import annotations

import html as htmlmod
import re
from urllib.parse import urljoin

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Suzuki"
HOME = "https://www.suzukicar.com.sg/"

_TAB = re.compile(r'<div class="mobile_menu">([^<]+)</div>')
_CARD = re.compile(r'class="model-area"\s+href="(/vehicles/[^"]+)"[^>]*>.*?<h3>([^<]+)</h3>', re.S)
_PRICE = re.compile(r'class="price-from[^"]*">\s*Price from:\s*</h1>\s*<h1 class="price-usd">\s*\$\s*([0-9][0-9,]*)')
_FUEL = {"HYBRID": "hybrid", "ELECTRIC VEHICLE (EV)": "ev"}


def models(menu_html: str) -> dict[str, tuple[str, str]]:
    """{model page url: (model name, fuel)} from the menu. A model under a fuel tab takes that fuel."""
    out: dict[str, tuple[str, str]] = {}
    parts = _TAB.split(menu_html)   # [before, tab, block, tab, block, ...]
    for tab, block in zip(parts[1::2], parts[2::2]):
        fuel = _FUEL.get(tab.strip().upper(), "")
        for href, name in _CARD.findall(block):
            url = urljoin(HOME, href)
            prev = out.get(url, ("", ""))
            out[url] = (htmlmod.unescape(name).strip(), prev[1] or fuel)
    return out


def parse(menu_html: str, pages: dict[str, str]) -> list[OfficialPrice]:
    """Rows from the menu page and {model page url: html}. A page without the price block is left out."""
    rows = []
    for url, (name, fuel) in models(menu_html).items():
        m = _PRICE.search(pages.get(url, ""))
        if not m:
            continue
        rows.append(OfficialPrice(make=BRAND, model=name, variant="", price=int(m.group(1).replace(",", "")),
                                  fuel=fuel, source_url=url, note="Price from: the cheapest variant of the model"))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    menu = s.page(HOME)
    return parse(menu, {url: s.page(url) for url in models(menu)})
