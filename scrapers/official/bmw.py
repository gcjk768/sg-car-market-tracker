"""BMW Singapore: the monthly price list PDF linked from the e-brochures page.

The PDF is a table: a model header line ("BMW 5 Series Sedan"), then one row per variant:
code, optional line or package, fuel use (l/100km) and or energy use (kWh/100 km), VES band, price.
The footer says the recommended retail price includes COE and VES.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin

from scrapers.base import FetchError
from scrapers.official import OfficialPrice, OfficialScraper

BRAND = "BMW"
INDEX_URL = "https://www.bmw.com.sg/en/topics/offers-and-services/e-brochures.html"

_LINK = re.compile(r"""[^"'\s()]*pdfs/pricelist/[^"'\s()]*Pricelist[^"'\s()]*\.pdf""")
_CELL = re.compile(r"\S+(?: \S+)*")          # cells are split by two or more spaces
_PRICE = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_BAND = re.compile(r"^(?:A|B|C\d)$")
_NUM = re.compile(r"^\d+(?:\.\d+)?$")
_VALID = re.compile(r"Price list valid from [^\n]+")


def find_pricelist(html: str, base: str = INDEX_URL) -> str | None:
    """The current price list PDF from the links on a BMW page. File names start with the date
    (2026.10.07-BMW-SG-Pricelist.pdf...), so the highest name is the newest."""
    links = sorted({m.split("/")[-1]: m for m in _LINK.findall(html)}.items())
    return urljoin(base, links[-1][1]) if links else None


def _plain(text: str) -> str:
    """"Coupé" as Sgcarmart writes it, "Coupe"."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def parse(text: str, source_url: str = "") -> list[OfficialPrice]:
    """Rows of the price list's layout text (pypdf extraction_mode="layout")."""
    valid = _VALID.search(text)
    note = valid.group().strip() if valid else ""
    coe = "with" if re.search(r"inclusive of COE", text) else "unknown"
    out: list[OfficialPrice] = []
    model, cut = "", None
    for line in text.splitlines():
        cells = [(m.start(), m.group()) for m in _CELL.finditer(line)]
        if not cells:
            continue
        if "Consumption" in line and "VES" in line:
            # Column split for this page: rows shift sideways as a whole, but the gap from a consumption
            # figure to the VES band cell is wide for l/100km and narrow for kWh/100 km. Midpoint of the
            # header's two gaps.
            cons = [p for p, c in cells if c == "Consumption"]
            ves = next(p for p, c in cells if c == "VES")
            cut = ves - sum(cons) / len(cons)
            continue
        if len(cells) == 1 and cells[0][1].startswith("BMW "):
            model = _plain(cells[0][1][4:])   # "BMW i" and "BMW M" are section titles, overwritten next line
            continue
        if len(cells) < 4 or not _PRICE.match(cells[-1][1]) or not _BAND.match(cells[-2][1]) or not model or cut is None:
            continue
        band_pos = cells[-2][0]
        mid = cells[1:-2]
        line_name = mid[0][1] if mid and not _NUM.match(mid[0][1]) else ""
        nums = [p for p, c in mid if _NUM.match(c)]
        if len(nums) != len(mid) - bool(line_name) or not nums:
            continue   # a cell we cannot place: leave the row out
        kinds = {"energy" if band_pos - p < cut else "fuel" for p in nums}
        fuel = "hybrid" if kinds == {"fuel", "energy"} else "ev" if kinds == {"energy"} else ""
        variant = f"{cells[0][1]} {line_name}".strip()
        out.append(OfficialPrice(make=BRAND, model=model, variant=variant, price=int(cells[-1][1].replace(",", "")),
                                 includes_coe=coe, fuel=fuel, source_url=source_url, note=note))
    return out


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    # The PDF name carries the list date and an asset id (".pdf.asset.1791518795005.pdf"), so it cannot be built
    # from today's date: no candidate names are tried, the link on the e-brochures page is the only source.
    # The plain HTML holds it as a literal href today; if it ever moves into script, the browser copy is the fallback.
    url = find_pricelist(s.page(INDEX_URL)) or find_pricelist(s.rendered(INDEX_URL))
    if not url:
        raise FetchError(f"no price list link on {INDEX_URL}")
    return parse(s.pdf(url), url)
