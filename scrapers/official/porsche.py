"""Porsche Singapore: every variant's "from" price on the models overview page.

The page is server rendered by Astro. One island's `props` attribute holds the whole model list as HTML escaped
JSON in Astro's serialised form, where `[0, x]` is a value and `[1, [...]]` an array. Each `modelCards` entry has
`modelName` (the variant), `modelSeries` ("Cayenne Coupé Model variants"), `engineType` and `price.value`.
The footnote reads "Price is without COE but includes a 5-year warranty package, ... and GST.", so includes_coe is
"without" when that line is on the page.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from typing import Any

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Porsche"
URL = "https://www.porsche.com/singapore/en/models/"

_PROPS = re.compile(r'props="([^"]*modelCards[^"]*)"')
FUEL = {"ELECTRIC": "ev", "HYBRID": "hybrid", "FUEL": "petrol"}
NOTE = "From price, without COE. Includes 5 year warranty, registration fee, estimated ARF, road tax, VES, EEAI and GST"


def _astro(v: Any) -> Any:
    """Undo Astro's serialisation: [0, x] is x, [1, [...]] is a list."""
    if isinstance(v, list) and v and isinstance(v[0], int):
        if v[0] == 0:
            return _astro(v[1]) if len(v) > 1 else None
        if v[0] == 1:
            return [_astro(x) for x in v[1]]
        return v
    if isinstance(v, dict):
        return {k: _astro(x) for k, x in v.items()}
    return v


def _clean(text: str) -> str:
    return " ".join(text.replace("Coupé", "Coupe").split())   # Sgcarmart writes "Coupe"


def parse(page: str, source_url: str = URL) -> list[OfficialPrice]:
    m = _PROPS.search(page)
    if not m:
        return []
    cards = _astro(json.loads(htmllib.unescape(m.group(1)))).get("modelCards") or []
    coe = "without" if "Price is without COE" in page else "unknown"
    rows = []
    for card in cards:
        car = card.get("model") or {}
        price = (car.get("price") or {}).get("value")
        name, series = car.get("modelName"), car.get("modelSeries") or ""
        if not (name and isinstance(price, int) and price > 0):
            continue
        rows.append(OfficialPrice(
            make=BRAND, model=_clean(series.removesuffix(" Model variants")) or _clean(car.get("modelRange") or ""),
            variant=_clean(name), price=price, includes_coe=coe, fuel=FUEL.get(car.get("engineType"), ""),
            source_url=source_url, note=NOTE))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    return parse(s.page(URL))
