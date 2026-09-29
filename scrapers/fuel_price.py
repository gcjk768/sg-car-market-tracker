"""Daily 95 octane pump price. Motorist.sg first, petrolprice.sg as fallback, config value last."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Optional

from models import FuelPrice
from scrapers.base import BaseScraper, ScraperUnavailable
from scrapers.parse_utils import parse_number, tables

log = logging.getLogger(__name__)


def parse_ron95_prices(html: str) -> dict[str, float]:
    """{brand: price per litre} for the 95 octane column of the first table that has one."""
    for headers, rows in tables(html):
        col = None
        for i, h in enumerate(headers):
            if re.search(r"\b95\b", h):
                col = i
                break
        if col is None:
            continue
        found: dict[str, float] = {}
        for row in rows:
            if len(row) <= col or not row[0]:
                continue
            price = parse_number(row[col])
            if price and 1.0 < price < 6.0:
                found[row[0]] = price
        if found:
            return found
    return {}


class FuelPriceScraper(BaseScraper):
    name = "fuel"

    def parse(self, html: str) -> list[FuelPrice]:
        prices = parse_ron95_prices(html)
        if not prices:
            raise ScraperUnavailable("no 95 octane table found")
        pick = self.cfg["costs"]["energy"]["ice"].get("price_pick", "min")
        value = min(prices.values()) if pick == "min" else sum(prices.values()) / len(prices)
        return [FuelPrice(observed_on=self.run_date, ron95_per_litre=round(value, 2), source=self._source, scraped_at=datetime.now())]

    def run(self) -> list[FuelPrice]:
        errors = []
        for key in ("fuel_price", "fuel_price_fallback"):
            url = self.cfg["sources"].get(key)
            if not url:
                continue
            self._source = url
            try:
                return self.parse(self.fetch(url))
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise ScraperUnavailable("; ".join(errors))


def scrape_fuel_price(cfg: dict[str, Any], user_agent: str, run_date: date, force: bool = False) -> Optional[FuelPrice]:
    scraper = FuelPriceScraper(cfg, user_agent, run_date, force)
    try:
        return scraper.run()[0]
    except Exception as exc:
        log.warning("fuel price unavailable: %s", exc)
        return None
    finally:
        scraper.close()
