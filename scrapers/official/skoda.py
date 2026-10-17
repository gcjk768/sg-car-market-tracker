"""Skoda Singapore (Skoda Centre Singapore): the pricelist page, read as plain HTML.

Each variant is a paragraph with the bold name ("Octavia Selection 1.5 TSI"), then a paragraph
"List Price: SGD X Retail Price: SGD Y Fuel Consumption ... VES Band ...". Every block shows twice (a carousel),
so rows are deduplicated. The retail price is the package price that bids for COE (the footer says "subject to
6 non-guaranteed COE bids"), so it is what a buyer pays; the list price is kept as list_price.
"""
from __future__ import annotations

import html
import re

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Skoda"
URL = "https://www.skoda.com.sg/offers-and-pricing/pricelist"

_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_PRICE = re.compile(r"^List Price:\s*SGD\s*(?P<list>[\d,]+)\s*Retail Price:\s*SGD\s*(?P<retail>[\d,]+)(?P<rest>.*)$")
# Skoda model names are one word, with "Combi" or "Coupe" for the body variants; the rest is the variant.
_NAME = re.compile(r"^(?P<model>\S+(?: Combi| Coupe)?) (?P<variant>.+)$")
_VALID = re.compile(r"All prices are effective from [^.]+\.")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse(page: str, source_url: str = URL) -> list[OfficialPrice]:
    paras = [_text(p) for p in _P.findall(page)]
    body = " ".join(paras)
    coe = "with" if re.search(r"subject to \d+ non.guaranteed COE bids", body, re.I) else "unknown"
    valid = _VALID.search(body)
    note = valid.group(0) if valid else ""
    rows: dict[str, OfficialPrice] = {}
    for name, para in zip(paras, paras[1:]):
        m = _PRICE.match(para)
        nm = _NAME.match(name)
        if not m or not nm or name in rows:
            continue
        if "Charge time" in m["rest"] or "kWh" in name:
            fuel = "ev"
        elif "TSI" in name:
            fuel = "petrol"
        else:
            fuel = ""
        rows[name] = OfficialPrice(make=BRAND, model=nm["model"], variant=nm["variant"],
                                   price=int(m["retail"].replace(",", "")), includes_coe=coe,
                                   list_price=int(m["list"].replace(",", "")), fuel=fuel,
                                   source_url=source_url, note=note)
    return list(rows.values())


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    return parse(s.page(URL))
