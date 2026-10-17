"""Volkswagen Singapore price list, read from the server rendered page www.volkswagen.com.sg/price-list.

Each card on the page reads: name, consumption and VES band, "List Price:", $amount, "Retail Price:", $amount.
The page states every model is COE included ("All Models COE Included"). The Retail Price is what a buyer pays;
the List Price is a higher figure kept in list_price. The page lists no packages and no validity dates.
"""
from __future__ import annotations

import html as htmllib
import re

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Volkswagen"
URL = "https://www.volkswagen.com.sg/price-list"

_MONEY = re.compile(r"^\$([\d,]+)$")


def _lines(page: str) -> list[str]:
    page = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
    out = []
    for chunk in re.split(r"<[^>]+>", page):
        for line in htmllib.unescape(chunk).replace("\xa0", " ").splitlines():
            if line.strip():
                out.append(" ".join(line.split()))
    return out


def _models(lines: list[str]) -> list[str]:
    """Model names from the site's own footer menu ("ICE Models" and "EV Models"), longest first."""
    start, end = lines.index("ICE Models"), lines.index("Support")
    return sorted((n for n in lines[start + 1:end] if n != "EV Models"), key=len, reverse=True)


def parse(page: str, source_url: str = URL) -> list[OfficialPrice]:
    lines = _lines(page)
    models = _models(lines)
    coe = "with" if any("COE Included" in line for line in lines) else "unknown"
    rows: list[OfficialPrice] = []
    section = ""
    for i, line in enumerate(lines):
        if line in ("Passenger Cars", "Electric Passenger Cars", "Commercial Vehicles"):
            section = line
            continue
        # A card: name, consumption, band, "List Price:", $n, "Retail Price:", $n
        if section == "Commercial Vehicles" or line != "List Price:" or i + 3 >= len(lines):
            continue   # commercial vans are out of scope for a passenger car tracker
        lst, label, ret = _MONEY.match(lines[i + 1]), lines[i + 2], _MONEY.match(lines[i + 3])
        name = next((lines[k] for k in range(i - 1, max(i - 5, -1), -1)
                     if not re.match(r"(Fuel|Energy) [Cc]onsumption|C?VES Band", lines[k])), "")
        model = next((m for m in models if name.startswith(m + " ")), None)
        if not (lst and ret and label == "Retail Price:" and model):
            continue   # never guess a pairing
        variant = name[len(model):].strip()
        ev = variant.endswith("(EV)") or "kWh" in lines[i - 2]
        variant = variant.removesuffix("(EV)").strip()
        fuel = ("ev" if ev else "hybrid" if "Hybrid" in variant else "diesel" if "TDI" in variant
                else "petrol" if "TSI" in variant else "")
        retail = int(ret.group(1).replace(",", ""))
        rows.append(OfficialPrice(make=BRAND, model=model, variant=variant, price=retail, includes_coe=coe,
                                  list_price=int(lst.group(1).replace(",", "")), fuel=fuel, source_url=source_url,
                                  note="Retail Price, subject to six non guaranteed COE bids"))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    return parse(s.page(URL))
