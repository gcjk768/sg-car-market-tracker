"""BYD prices from bydcars.sg (BYD x Sime Motors, distributor E-Auto).

The price list is a PDF renamed every two weeks (BYD-Price-List_8-Oct-21-Oct.pdf). No page on the site links it, so
scrape() asks the site's own WordPress media index for its newest PDFs (spec sheets too) and keeps the newest one
named like a price list.

Each PDF page is one table: model names along the top, then rows of prices per column. Read off the rendered list
(8 to 21 October 2026): list price, Savers (non guaranteed COE), Base (non guaranteed COE), Excite (guaranteed COE)
before its discount in small print, Excite after the discount. Trim names (Standard, Premium, Dynamic) are images,
not text, so the variant is the COE category with the energy or fuel figure the page prints for that column.
Columns are paired with names by their position on the line.
"""
from __future__ import annotations

import json
import re

from scrapers.official import OfficialPrice, OfficialScraper, cheapest

BRAND = "BYD"
# No search term: how WordPress matches one (titles, file names, plugins) is not ours to rely on.
# latest_pdf picks the price list out of the newest PDFs by file name.
MEDIA_URL = ("https://bydcars.sg/wp-json/wp/v2/media?mime_type=application/pdf"
             "&orderby=date&order=desc&per_page=20&_fields=date,source_url")
PACKAGES = ("Savers", "Base", "Excite")   # row order under the list price, as the PDF shows them

_PRICE_ROW = re.compile(r"\s*(?:\$[\d,]+\s*)+")
_PRICE = re.compile(r"\$([\d,]+)")
_NAME = re.compile(r"(?:\d{4}\s+)?BYD\b.*?(?=\s{3,}(?:\d{4}\s+)?BYD\b|\s*$)")   # one model name per match
_FIGURE = re.compile(r"(\d+(?:\.\d+)?)\s*(kWh|L)\s*/\s*100km(?:\s*\((\w+)\))?")
_EXTRA = re.compile(r"\b(?!VES)[A-Z]{3,}(?: [A-Z]{3,})*\b")   # words like EXTENDED RANGE under a model name
_VALID = re.compile(r"Valid from (.+?\d{4}, \d+[AP]M)")


def latest_pdf(media_json: str) -> str:
    """The newest price list PDF in the site's media index."""
    items = [m for m in json.loads(media_json) if "price-list" in m["source_url"].lower()]
    if not items:
        raise ValueError("no BYD price list in the media index")
    return max(items, key=lambda m: m["date"])["source_url"]


def _spans(line: str, rx: re.Pattern) -> list[tuple[float, re.Match]]:
    return [((m.start() + m.end()) / 2, m) for m in rx.finditer(line)]


def _nearest(x: float, spans: list[tuple[float, object]]):
    return min(spans, key=lambda s: abs(s[0] - x))[1]


def _model(name: str) -> tuple[str, str]:
    """(model, model year) from a printed name: '2025 BYD SEAL' is ('Seal', '2025')."""
    words = re.sub(r"(\d)([A-Za-z]{2,})", r"\1 \2", " ".join(name.split())).split()
    words = [w.capitalize() if w.isalpha() and len(w) > 2 else w for w in words if w != "BYD"]
    year = [w for w in words if re.fullmatch(r"\d{4}", w)]
    return " ".join(w for w in words if w not in year), (year[0] if year else "")


def parse(text: str, source_url: str = "") -> list[OfficialPrice]:
    """Rows from the PDF's layout text, one per price column."""
    if not all(f"{p} Package" in text for p in PACKAGES):
        raise ValueError("BYD package names changed, check the row order")
    valid = _VALID.search(text)
    note = f"Valid from {re.sub(r'\s+', ' ', valid.group(1)).replace(' – ', ' to ')}" if valid else ""
    note += ". Package prices are after finance, insurance and trade in rebates."
    out: list[OfficialPrice] = []
    for block in re.split(r"(?=Valid from )", text):
        lines = block.splitlines()
        head = next((i for i, l in enumerate(lines) if re.search(r"\bModel\b", l) and "BYD" in l), None)
        cat = re.search(r"CATEGORY ([A-E])", block)
        if head is None or not cat:
            continue
        rows = [l for l in lines[head:] if _PRICE_ROW.fullmatch(l)][:5]
        cols = [_spans(l, _PRICE) for l in rows]
        if len(rows) < 5 or len({len(c) for c in cols}) != 1:
            raise ValueError(f"BYD Cat {cat.group(1)} table layout changed")
        first = lines.index(rows[0], head)
        names = [(x, m.group(0)) for x, m in _spans(lines[head], _NAME)]
        figures, extras = [], []
        for l in lines[head + 1:first]:
            figures += _spans(l, _FIGURE)
            extras += [(x, m.group(0)) for x, m in _spans(l, _EXTRA)]
        for x, word in extras:   # a second name line belongs to the name above it
            i = min(range(len(names)), key=lambda k: abs(names[k][0] - x))
            names[i] = (names[i][0], f"{names[i][1]} {word}")
        used = set()
        for i, (x, _) in enumerate(cols[0]):
            fig = _nearest(x, figures) if figures else None
            if fig is not None and id(fig) in used:
                raise ValueError(f"BYD Cat {cat.group(1)} column {i + 1} has no figure of its own")
            used.add(id(fig))
            model, year = _model(_nearest(x, names))
            parts = [year, fig and fig.group(3), f"Cat {cat.group(1)}", fig and f"{fig.group(1)} {fig.group(2)}/100km"]
            nums = [int(c[i][1].group(1).replace(",", "")) for c in cols]
            # Excite prints the pre discount price above the price paid: the lower of the two is what a buyer pays
            packages = {"Savers": nums[1], "Base": nums[2], "Excite": min(nums[3], nums[4])}
            out.append(OfficialPrice(
                make=BRAND, model=model, variant=", ".join(p for p in parts if p),
                price=cheapest(packages, nums[0]), includes_coe="with", list_price=nums[0], packages=packages,
                fuel="hybrid" if "DM-i" in model else "ev" if fig and fig.group(2) == "kWh" else "",
                source_url=source_url, note=note))
    return out


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    # ponytail: no guessed file names, the media index has listed every price list since August 2026
    url = latest_pdf(s.page(MEDIA_URL))
    return parse(s.pdf(url), url)
