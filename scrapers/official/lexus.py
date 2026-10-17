"""Lexus Singapore (Borneo Motors): lexus.com.sg.

Prices sit in JSON inside the plain HTML (`window.data_global_models`, the site wide model menu), not in the visible
text, which Vue draws in the browser. That menu lists every model with a numeric `price`; the price list block on the
same page leaves some models blank (ES 350h, RZ, LM 350h), so the menu is the one source read here.
"P.O.A" models (IS 300h) have no price and are left out. No COE wording on the list, so includes_coe stays "unknown".
"""
from __future__ import annotations

import json

from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Lexus"
URL = "https://www.lexus.com.sg/en/price-and-model-tools/price-list.html"
MARKER = "window.data_global_models = "
FUEL = {"electric": "ev", "hybrid": "hybrid", "pluginhybrid": "hybrid"}   # "performance" alone says nothing about fuel


def parse(html: str, source_url: str = URL) -> list[OfficialPrice]:
    start = html.find(MARKER)
    if start < 0:
        raise ValueError("Lexus model data not found on the page")
    groups, _ = json.JSONDecoder().raw_decode(html, start + len(MARKER))
    rows: list[OfficialPrice] = []
    seen: set[str] = set()
    for group in groups:
        for car in group.get("cars", []):
            mp = car.get("mpdata") or {}
            name = (mp.get("modelname") or "").strip().removeprefix("The All-New ").strip()
            if not mp.get("price") or not name or name in seen:
                continue
            seen.add(name)
            fuels = {FUEL[e] for e in mp.get("engine") or [] if e in FUEL}
            rows.append(OfficialPrice(
                make=BRAND, model=group["title"].strip().upper(), variant=name, price=int(mp["price"]),
                fuel=fuels.pop() if len(fuels) == 1 else "", source_url=mp.get("url") or source_url,
                note="starting price ('From')" if (mp.get("fromtext") or "").strip() == "From" else ""))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    return parse(s.page(URL))
