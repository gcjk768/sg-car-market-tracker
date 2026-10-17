"""Citroen Singapore (Cycle & Carriage): citroen.com.sg.

The home page's "View Price List" button links a short link (cyclecarriage.short.gy/citroen-price-list) that
redirects to a dated PDF on the distributor's CMS (citroen_pc-pricelist-8-oct-21-oct.pdf when this was written).
scrape() reads the link from the home page and follows the redirect on every run, so no dated name is hard coded.

The PDF layout text has one row per variant: "CITROËN <model> <variant>" with the variant sometimes wrapped onto the
price line, then the VES band, the energy use and the "Body Price (After VES & EEAI)" as the first $ amount. The
rebate columns after it need in house finance, insurance or a trade in, so the nett figure only goes in note.
A body price is the car without COE, so includes_coe is "without". Energy in kWh means the car is electric.

A price list whose end date has passed is not what a buyer pays today: scrape() reports it unavailable.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Optional

from scrapers.base import FetchError
from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "Citroen"
HOME = "https://www.citroen.com.sg/"
SHORT_LINK = "https://cyclecarriage.short.gy/citroen-price-list"   # used only if the home page stops linking it

LINK = re.compile(r'href="(https://cyclecarriage\.short\.gy/[^"]*price[^"]*)"', re.I)
NAME = re.compile(r"^\s*CITROËN\s+(\S+)\s+(.*?)\s*$")                 # "CITROËN ë-C4X   SHINE"
PRICE_ROW = re.compile(r"^\s*(.*?)\s*\b([A-C]\d?)\s+[\d.]+\s*kWh/\s*100km\s+\$(\d{1,3}(?:,\d{3})+)((?:\s+\$[\d,]+)*)\s*$")
# ponytail: rows need energy in kWh, so only electric cars are read (the only kind listed today); widen for L/100km
# rows and set fuel per row if a petrol model comes back.
MONEY = re.compile(r"\$(\d{1,3}(?:,\d{3})+)")
VALID = re.compile(r"Effective\s+date:\s*(.+)", re.I)   # the layout spreads the label over a wide gap
DAY = re.compile(r"\d{1,2} [A-Za-z]{3,9} \d{4}")


def _amount(s: str) -> int:
    return int(s.replace(",", ""))


def _text(pdf_text: str) -> str:
    return unicodedata.normalize("NFKC", pdf_text)


def _validity(text: str) -> str:
    """The "Effective date" range with the layout's runs of spaces squeezed, or "" when the PDF gives none."""
    m = VALID.search(text)
    return " ".join(m.group(1).split()) if m else ""


def price_list_link(html: str) -> str:
    m = LINK.search(html)
    return m.group(1) if m else SHORT_LINK


def valid_until(pdf_text: str) -> Optional[date]:
    """Last day of the "Effective date" range, or None when the PDF gives none."""
    days = DAY.findall(_validity(_text(pdf_text)))
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(days[-1], fmt).date()
        except (ValueError, IndexError):
            pass
    return None


def parse(pdf_text: str, source_url: str = SHORT_LINK) -> list[OfficialPrice]:
    text = _text(pdf_text)
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    valid = _validity(text)
    note = f"Effective {valid.replace(' - ', ' to ')}. " if valid else ""
    rows: list[OfficialPrice] = []
    for i, line in enumerate(lines[:-1]):
        name, row = NAME.match(line), PRICE_ROW.match(lines[i + 1])
        if not (name and row) or "$" in line:
            continue   # captions ("Citroën ë-C4X Electric Sedan") carry no price row; never guess a pairing
        variant = " ".join(f"{name.group(2)} {row.group(1)}".split()).title()
        body = _amount(row.group(3))
        rest = MONEY.findall(row.group(4))
        nett = f". Nett effective body price ${rest[-1]} after in house finance, insurance and trade in rebates" if rest else ""
        rows.append(OfficialPrice(
            make=BRAND, model=name.group(1), variant=variant, price=body, includes_coe="without", list_price=body,
            fuel="ev", source_url=source_url, note=f"{note}Body price (after VES and EEAI){nett}"))
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    url = price_list_link(s.page(HOME))
    text = s.pdf(url)
    end = valid_until(text)
    if end and end < s.run_date:
        raise FetchError(f"the linked price list expired on {end.isoformat()}: {url}")
    return parse(text, url)
