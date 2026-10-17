"""Prices from each brand's own Singapore website, read before Sgcarmart's.

One module per brand in this package (`scrapers/official/<brand>.py`). Each has:

* `BRAND`: the make as Sgcarmart and the site write it ("BYD", "Kia", "Mercedes-Benz").
* `parse(...)`: pure, from page text or PDF text to `OfficialPrice` rows. Tested against a saved copy of the live page
  under `fixtures/official/`.
* `scrape(s: OfficialScraper) -> list[OfficialPrice]`: fetches with the helpers below and calls `parse`.

Fetch rules (the owner's, 2026-10-04 and 2026-10-08): robots.txt first, the shared throttle and day cache, the app's
own user agent. A plain request first; the headless browser only for pages that draw prices by script. Not Jina
Reader: its robots.txt bars every agent but live user fetches (checked 2026-10-17), and this app honours robots.txt.
A 403, 429, captcha or "verify you are human" page marks the brand blocked for the day: never retried around.

Which number: Singapore distributors often print a list price with a full COE and then cheaper "packages" that bid
for COE (BYD Savers or Base, Kia Standard or Gold). Buyers pay the package price, and Sgcarmart quotes it, so
`price` is the cheapest package when packages exist and the listed price otherwise. `list_price` and `packages`
keep the rest so the site can show them.
"""
from __future__ import annotations

import importlib
import io
import logging
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import quote, urlsplit

from scrapers.base import BaseScraper, FetchError, RobotsDisallowed

log = logging.getLogger(__name__)

BLOCK_MARKERS = ("just a moment", "verify you are human", "captcha", "access denied", "attention required")


@dataclass
class OfficialPrice:
    make: str
    model: str
    variant: str
    price: int                       # what a buyer pays: the cheapest package, else the listed price
    includes_coe: str = "unknown"    # "with", "without" or "unknown", as the page states it
    list_price: Optional[int] = None
    packages: dict[str, int] = field(default_factory=dict)   # package name to price
    fuel: str = ""                   # "ev", "hybrid", "petrol", "diesel" when the page says
    source_url: str = ""
    note: str = ""                   # validity dates or anything a buyer should know

    def as_row(self) -> dict[str, Any]:
        return {"make": self.make, "model": self.model, "variant": self.variant, "price": self.price,
                "includes_coe": self.includes_coe, "list_price": self.list_price, "packages": self.packages,
                "fuel": self.fuel, "source_url": self.source_url, "note": self.note}


class Blocked(Exception):
    """The site answered with a block or challenge page. The brand is skipped for the day."""


class OfficialScraper(BaseScraper):
    """Fetch helpers for the brand modules. All go through BaseScraper's robots check, throttle and day cache."""

    name = "official"

    def parse(self, html: str) -> list:   # brand modules parse; the base class wants the method
        return []

    def run(self) -> list:
        return []

    def page(self, url: str) -> str:
        """Plain GET of a page."""
        text = self.fetch(url)
        if any(m in text[:3000].lower() for m in BLOCK_MARKERS) and len(text) < 20000:
            raise Blocked(url)
        return text

    def pdf(self, url: str, layout: bool = True) -> str:
        """Text of a PDF, cached as text. layout=False uses pypdf's plain mode, for PDFs whose layout mode finds no
        text (MG's)."""
        key = url if layout else url + "#plain"
        cached = self._read_cache(key)
        if cached is not None:
            return cached
        if not self._allowed(url):
            raise RobotsDisallowed(url)
        from pypdf import PdfReader

        BaseScraper._throttle.wait(urlsplit(url).netloc)
        log.info("GET %s", url)
        resp = self.client.get(quote(url, safe=":/?=&%"))
        if resp.status_code in (403, 429):
            raise Blocked(url)
        resp.raise_for_status()
        mode = "layout" if layout else "plain"
        text = "\n".join(p.extract_text(extraction_mode=mode) or "" for p in PdfReader(io.BytesIO(resp.content)).pages)
        self._write_cache(key, text)
        return text

    def rendered(self, url: str, wait_selector: str | None = None) -> str:
        """A page drawn by script, through headless Chromium (BaseScraper.fetch_rendered)."""
        text = self.fetch_rendered(url, wait_selector=wait_selector, wait_ms=4000)
        if any(m in text[:5000].lower() for m in BLOCK_MARKERS) and len(text) < 30000:
            raise Blocked(url)
        return text


def brand_modules() -> list[Any]:
    """Every brand module in this package, alphabetical."""
    import scrapers.official as pkg

    mods = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if not info.name.startswith("_"):
            mods.append(importlib.import_module(f"scrapers.official.{info.name}"))
    return mods


def scrape_official(cfg: dict[str, Any], user_agent: str, run_date, force: bool = False,
                    only: Optional[list[str]] = None) -> tuple[list[OfficialPrice], dict[str, str]]:
    """(prices, {brand: status}) for every brand module. A brand that fails or is blocked never stops the rest."""
    scraper = OfficialScraper(cfg, user_agent, run_date, force)
    prices: list[OfficialPrice] = []
    status: dict[str, str] = {}
    try:
        for mod in brand_modules():
            if only and mod.BRAND not in only:
                continue
            try:
                found = mod.scrape(scraper)
                prices.extend(found)
                status[mod.BRAND] = f"{len(found)} prices"
            except Blocked as exc:
                status[mod.BRAND] = f"blocked: {exc}"
                log.warning("official %s blocked: %s", mod.BRAND, exc)
            except (RobotsDisallowed, FetchError) as exc:
                status[mod.BRAND] = f"unavailable: {exc}"
                log.warning("official %s unavailable: %s", mod.BRAND, exc)
            except Exception as exc:   # a site changed layout: Sgcarmart covers it today
                status[mod.BRAND] = f"failed: {exc}"
                log.warning("official %s failed: %s", mod.BRAND, exc, exc_info=True)
    finally:
        scraper.close()
    return prices, status


def cheapest(packages: dict[str, int], fallback: int) -> int:
    """The price a buyer pays: the cheapest package when there are packages."""
    vals = [v for v in packages.values() if v]
    return min(vals) if vals else fallback


Parser = Callable[..., list[OfficialPrice]]
