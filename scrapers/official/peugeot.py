"""Peugeot Singapore (Cycle & Carriage): peugeot.sg.

The price list page is plain HTML whose "View Prices" button links a dated PDF
(AP2502_Peugeot_Price_list_6Feb-19Feb_2025.pdf when this was written). scrape() reads that link from the page on every
run, so no dated name is hard coded and no candidate names are guessed. The PDF layout text has one row per variant:
the name in the first column (sometimes wrapped onto the next line), then the list price as the first $ amount.
The rebate columns after it need in house finance, insurance or a trade in, so they are left out.

Prices are non guaranteed COE packages ("For non-COE package price ... COE charged amount stated, will be deducted"),
so includes_coe is "with". The guaranteed COE option costs the stated top up more and is kept as a second package.
The PDF does not name the fuel, so fuel stays empty.

A price list whose end date has passed is not what a buyer pays today: scrape() reports it unavailable and Sgcarmart
covers the brand. The distributor's newer PDF (cyclecarriage.com) is not linked from any static page, so it is not read.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin

from scrapers.base import FetchError
from scrapers.official import OfficialPrice, OfficialScraper, cheapest

BRAND = "Peugeot"
PAGE = "https://www.peugeot.sg/buy/pricelist.html"

PDF_LINK = re.compile(r'href="([^"]*pricelist[^"]*\.pdf)"', re.I)
ROW = re.compile(r"^\s*((?:e-)?\d{3,4}) (\S.*?)(?:\s{3,}|$)")          # "408 1.2 GT Fastback" then a column gap
WRAP = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?)(?:\s{3,}|$)")              # the rest of a wrapped name, e.g. "Fastback"
MONEY = re.compile(r"\$(\d{1,3}(?:,\d{3})+)")
TOPUP = re.compile(r"Guaranteed\s+COE\s+Topup\s*:\s*\$(\d{1,3}(?:,\d{3})+)", re.I)
VALID = re.compile(r"Effective date:\s*(.+)", re.I)
DAY = re.compile(r"\d{1,2} [A-Za-z]{3,9} \d{4}")


def _amount(s: str) -> int:
    return int(s.replace(",", ""))


def _text(pdf_text: str) -> str:
    return unicodedata.normalize("NFKC", pdf_text)   # the PDF writes "Effective" with an "ff" ligature


def pdf_link(html: str) -> str:
    """The price list PDF the page links today."""
    m = PDF_LINK.search(html)
    if not m:
        raise ValueError("Peugeot price list page links no PDF")
    return urljoin(PAGE, m.group(1))


def valid_until(pdf_text: str) -> Optional[date]:
    """Last day of the "Effective date" range, or None when the PDF gives none."""
    m = VALID.search(_text(pdf_text))
    days = DAY.findall(m.group(1)) if m else []
    if not days:
        return None
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(days[-1], fmt).date()
        except ValueError:
            pass
    return None


def parse(pdf_text: str, source_url: str = PAGE) -> list[OfficialPrice]:
    text = _text(pdf_text)
    lines = text.splitlines()
    valid = VALID.search(text)
    note = "Effective " + valid.group(1).strip().replace(" - ", " to ") if valid else ""
    topup = TOPUP.search(text)
    rows: list[OfficialPrice] = []
    for i, line in enumerate(lines):
        name, price = ROW.match(line), MONEY.search(line)
        if not (name and price):
            continue   # captions repeat the names without prices
        variant = name.group(2)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        wrap = WRAP.match(nxt)
        if wrap and "$" not in nxt:
            variant += " " + wrap.group(1)
        listed = _amount(price.group(1))
        packages = {"Non-Guaranteed COE": listed, "Guaranteed COE": listed + _amount(topup.group(1))} if topup else {}
        rows.append(OfficialPrice(
            make=BRAND, model=name.group(1), variant=variant, price=cheapest(packages, listed), includes_coe="with",
            list_price=listed, packages=packages, source_url=source_url, note=note))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    url = pdf_link(s.page(PAGE))
    text = s.pdf(url)
    end = valid_until(text)
    if end and end < s.run_date:
        raise FetchError(f"the linked price list expired on {end.isoformat()}: {url}")
    return parse(text, url)
