"""Top selling makes in Singapore from LTA's monthly new car registrations (table M03).

LTA publishes the table as a PDF with one row per make, importer type and fuel type, and one
half year per page group. The first number on a row is that half year's total, so summing the
rows by make gives the year to date. LTA does not publish registrations by model.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date
from typing import Any, Optional

from scrapers.base import BaseScraper, RobotsDisallowed, ScraperUnavailable

log = logging.getLogger(__name__)

ROW = re.compile(r"^(?P<make>[A-Z][A-Z0-9 .&'/]*?) (?:AMD|PI) (?P<fuel>[A-Za-z][A-Za-z() \-]*?) (?P<total>\d+)(?: |$)")
# No word boundaries: the PDF runs month headers together, as in "2026-022026-01".
MONTH = re.compile(r"(20\d\d)-(0[1-9]|1[0-2])")


def parse_registrations(text: str) -> tuple[dict[str, dict[str, int]], list[str]]:
    """({make: {"total": n, "ev": n}}, sorted months covered) from the table's text."""
    makes: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        make = m["make"].replace(".", "").strip()
        row = makes.setdefault(make, {"total": 0, "ev": 0})
        n = int(m["total"])
        row["total"] += n
        if m["fuel"].strip() == "Electric":
            row["ev"] += n
    months = sorted({f"{y}-{mo}" for y, mo in MONTH.findall(text)})
    return makes, months


class RegistrationsScraper(BaseScraper):
    name = "registrations"

    def pdf_text(self, url: str) -> str:
        # fetch() is text only, so the PDF is fetched here and its extracted text is cached instead.
        cached = self._read_cache(url)
        if cached is not None:
            return cached
        if not self._allowed(url):
            raise RobotsDisallowed(url)
        from pypdf import PdfReader
        from urllib.parse import urlsplit

        BaseScraper._throttle.wait(urlsplit(url).netloc)
        log.info("GET %s", url)
        resp = self.client.get(url)
        resp.raise_for_status()
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(resp.content)).pages)
        self._write_cache(url, text)
        return text

    def parse(self, text: str) -> list:
        makes, months = parse_registrations(text)
        if not makes:
            raise ScraperUnavailable("no make rows found in the LTA registrations table")
        return [makes, months]

    def run(self) -> list:
        return self.parse(self.pdf_text(self.cfg["sources"]["lta_registrations_by_make"]))


def scrape_registrations(cfg: dict[str, Any], user_agent: str, run_date: date, force: bool = False) -> Optional[tuple[dict[str, dict[str, int]], list[str]]]:
    scraper = RegistrationsScraper(cfg, user_agent, run_date, force)
    try:
        makes, months = scraper.run()
        return makes, months
    except Exception as exc:
        log.warning("registrations unavailable: %s", exc)
        return None
    finally:
        scraper.close()
