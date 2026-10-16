"""Every new car on Sgcarmart's price list, petrol and hybrid included, for the website.

Sgcarmart's new car listing (`sources.sgcarmart_new_listing`) is a React page whose server payload
carries `defaultSearchList`: 15 models per page with every variant's price, power, fuel economy and
transmission. `?page=N` on the search URL pages server side (checked 2026-10-17), so the whole list is
about 38 pages at the site's 30 second crawl delay, once a day, cached. No model page is opened. The
electric models are here too, but the EV scraper's model pages carry range and battery, so the site
prefers those.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any, Optional

from models import NewCarVariant
from scrapers.base import BaseScraper, ScraperUnavailable
from scrapers.used_common import is_commercial_model

log = logging.getLogger(__name__)

CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
PAGES = re.compile(r">\s*\d+\s*(?:<!-- -->)?\s*of\s*(?:<!-- -->)?\s*(\d+)\s*<")
LITRES = re.compile(r"(?<![\d.])(\d\.\d)(?![\d.])")
GOODS = re.compile(r"\b(van|truck|lorry|bus|minibus|pick[- ]?up|cargo|chassis|tipper|prime mover|tractor|coach)\b", re.I)


def _payload_text(html: str) -> str:
    """The decoded React payload chunk that holds the search list, or an empty string."""
    for m in CHUNK.finditer(html):
        if "defaultSearchList" in m.group(1):
            return json.loads('"' + m.group(1) + '"')
    return ""


def parse_search_list(html: str) -> list[dict[str, Any]]:
    """The page's models as plain dicts: id, make, model, slug, launchDate, subModels[...]."""
    text = _payload_text(html)
    key = '"defaultSearchList":'
    k = text.find(key)
    if k < 0:
        return []
    k += len(key)
    depth = 0
    for j in range(k, len(text)):
        if text[j] == "[":
            depth += 1
        elif text[j] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[k:j + 1])
    return []


def parse_total_pages(html: str) -> int:
    m = PAGES.search(html)
    return int(m.group(1)) if m else 1


def fuel_of(slug: str, economy: str | None) -> str:
    """ev, hybrid, diesel or petrol from the slug and the listed economy unit."""
    low = slug.lower()
    if "km/kwh" in (economy or "").lower() or "electric" in low:
        return "ev"
    if "hybrid" in low or "phev" in low:
        return "hybrid"
    if "diesel" in low:
        return "diesel"
    return "petrol"


def engine_cc_estimate(*texts: str | None) -> Optional[int]:
    """Engine size from a litre figure in the variant or model name ("1.5 Turbo" is 1500 cc). Sgcarmart's
    list does not carry the exact capacity, so road tax from this is an estimate."""
    for t in texts:
        m = LITRES.search(t or "")
        if m and 0.6 <= float(m.group(1)) <= 8.0:
            return int(round(float(m.group(1)) * 1000))
    return None


class SgcarmartNewCarScraper(BaseScraper):
    name = "sgcarmart_new_cars"

    def __init__(self, cfg, user_agent, run_date=None, force=False):
        super().__init__(cfg, user_agent, run_date, force)
        self.ncfg = cfg.get("new_cars", {})
        self.url = cfg["sources"]["sgcarmart_new_listing"]
        self.exclude_makes = {m.lower() for m in self.ncfg.get("exclude_makes", [])}
        self.exclude_models = cfg["used"]["filters"].get("exclude_models", []) + self.ncfg.get("exclude_models", [])

    def parse(self, html: str) -> list[NewCarVariant]:
        out: list[NewCarVariant] = []
        for m in parse_search_list(html):
            make, model = str(m.get("make") or "").strip(), str(m.get("model") or "").strip()
            if not make or not model or make.lower() in self.exclude_makes:
                continue
            if GOODS.search(model) or is_commercial_model(f"{make} {model}", self.exclude_models):
                continue
            props = m.get("additionalProperties") or {}
            if props.get("isUpcoming") or props.get("isAvailable") is False:
                continue
            url = f"https://www.sgcarmart.com/new-cars/info/{m.get('id')}/{m.get('slug', '')}"
            for s in m.get("subModels") or []:
                price = s.get("price")
                if not price or s.get("isAvailable") is False:
                    continue
                flux = s.get("priceFluctuation") or {}
                power_bhp = s.get("power")
                out.append(NewCarVariant(
                    make=make, model=model, variant=str(s.get("name") or "").strip(),
                    fuel=fuel_of(str(m.get("slug") or ""), s.get("fuelEconomy")),
                    price_with_coe=int(price), price_includes_coe=bool(s.get("withCoe", True)),
                    power_kw=round(float(power_bhp) * 0.7457, 1) if power_bhp else None,
                    engine_cc=engine_cc_estimate(str(s.get("name") or ""), model),
                    fuel_economy=s.get("fuelEconomy") or None, transmission=s.get("transmission") or None,
                    price_change=int(flux["amount"]) if flux.get("amount") else None,
                    price_change_on=flux.get("date") or None,
                    launch_date=m.get("launchDate") or None, model_id=int(m.get("id") or 0) or None,
                    listing_url=url, source=self.name, scraped_at=datetime.now(),
                ))
        return out

    def run(self) -> list[NewCarVariant]:
        first = self.fetch(self.url.format(page=1))
        out = self.parse(first)
        pages = min(parse_total_pages(first), int(self.ncfg.get("max_pages", 45)))
        if not parse_search_list(first):
            raise ScraperUnavailable("sgcarmart new car listing: no search list in the page, layout may have changed")
        for page in range(2, pages + 1):
            try:
                out.extend(self.parse(self.fetch(self.url.format(page=page))))
            except Exception as exc:  # one bad page never loses the rest
                log.warning("new car listing page %d failed: %s", page, exc)
        if not out:
            raise ScraperUnavailable("sgcarmart new car listing: no variants parsed")
        return out


def scrape_new_cars(cfg: dict[str, Any], user_agent: str, run_date: date, force: bool = False) -> list[NewCarVariant]:
    scraper = SgcarmartNewCarScraper(cfg, user_agent, run_date, force)
    try:
        return scraper.run()
    finally:
        scraper.close()
