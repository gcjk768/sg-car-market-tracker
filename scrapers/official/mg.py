"""MG Singapore (Eurokars EV) price list: a dated PDF linked from the home page, read straight from mg.com.sg.

The file name carries its validity dates ("MG Pricelist Valid 20261008 - 20261021.pdf") and changes about every two
weeks, so scrape() finds the current link on the home page. OfficialScraper.pdf() gives an empty string for this
PDF (pypdf layout mode finds no text in it), so it is read with s.pdf(url, layout=False), pypdf's plain mode.

Each table row reads: COE category, list price, rebates in brackets, the Guaranteed package, then the Non Guaranteed
package or "-". Plain mode flattens the table, so the model name sits inside the row on most lines but lands on
its own before the row (CYBERSTER AWD) or after it (MG4 URBAN) on others. A row takes the one name between it and its
neighbours that no other row claims; a row with no single such name is left out rather than guessed.
Both packages are COE bid packages ("Guaranteed Package: 1 bid, $123,000 rebate"), so includes_coe is "with"
when the terms list them, else "unknown". The rebates per COE category are kept in note.

Model names follow Sgcarmart, which the matcher compares against: "MGS5" is "S5", and "MG4 URBAN" is the model
"4 Urban" with no trim, because the page gives none. The page names the Cyberster RWD by its power
("RWD 250kW") while Sgcarmart lists it as "77 kWh(A)": no shared word, so it pairs on price alone.
"""
from __future__ import annotations

import io
import re
from urllib.parse import quote, urljoin, urlsplit

from scrapers.base import BaseScraper, RobotsDisallowed
from scrapers.official import Blocked, OfficialPrice, OfficialScraper, cheapest

BRAND = "MG"
HOME = "https://mg.com.sg/"

_LINK = re.compile(r'href="([^"]*Pricelist[^"]*\.pdf)"', re.I)
_ROW = re.compile(r"\b([A-E])\s+\$([\d,]{6,})([^$]*?)(?:\(\$[\d,]+\)\s*)+\$([\d,]+)\s*(\$[\d,]+|-)")
# "MGS5 LUXURY", "MG4 URBAN", "CYBERSTER RWD 250kW". Upper case only, so prose like "MG4 Urban" or "MG Pre-Car" never matches.
_NAME = re.compile(r"\b(MGS?\d\w*|CYBERSTER)\s+([A-Z]{2,}\b(?:\s+\d+kW)?)")
_VALID = re.compile(r"Pricelist valid from ([^\n]+?)\s*\n")


def pricelist_url(html: str) -> str | None:
    """The current price list PDF linked from the home page, URL encoded."""
    m = _LINK.search(html)
    return quote(urljoin(HOME, m.group(1)), safe=":/%") if m else None


def _num(s: str) -> int:
    return int(s.replace("$", "").replace(",", ""))


def _case(text: str) -> str:
    """"LUXURY" to "Luxury" word by word; short codes and words with digits ("RWD", "250kW", "S5") stay as written."""
    return " ".join(w if len(w) <= 3 or any(c.isdigit() for c in w) else w.title() for w in text.split())


def parse(text: str, source_url: str = "") -> list[OfficialPrice]:
    rows = list(_ROW.finditer(text))
    inside = [_NAME.findall(r.group(3)) for r in rows]
    claimed = {n for names in inside if len(names) == 1 for n in names}
    valid = _VALID.search(text)
    fuel = "ev" if re.search(r"Electric\s+Model", text) else ""
    coe = "with" if re.search(r"Guaranteed Package:\s*\d+\s*bid", text) else "unknown"
    out = []
    for i, r in enumerate(rows):
        names = inside[i]
        if len(names) != 1:   # name outside the row: look in the gaps to the neighbouring rows
            start = rows[i - 1].end() if i else 0
            end = rows[i + 1].start() if i + 1 < len(rows) else len(text)
            names = [n for n in _NAME.findall(text[start:r.start()] + "\n" + text[r.end():end]) if n not in claimed]
            if len(set(names)) != 1:
                continue
            claimed.add(names[0])
        model, variant = names[0]
        model = re.sub(r"^MG(?=S?\d)", "", model)   # "MGS5" to "S5", "MG4" to "4"
        if variant == "URBAN":   # "MG4 URBAN" is the car's name, not a trim
            model, variant = f"{model} {variant}", ""
        cat, gtd, non_gtd = r.group(1), _num(r.group(4)), r.group(5)
        packages = {"Guaranteed COE": gtd}
        if non_gtd != "-":
            packages["Non Guaranteed COE"] = _num(non_gtd)
        terms = re.search(rf"Category {cat}[.,]\s*([^\n]+)", text)
        note = " ".join(filter(None, [
            f"Valid from {valid.group(1).replace(' – ', ' to ')}." if valid else "",
            f"COE Cat {cat}. {terms.group(1).strip()}" if terms else f"COE Cat {cat}.",
        ]))
        list_price = _num(r.group(2))
        out.append(OfficialPrice(
            make=BRAND, model=_case(model), variant=_case(variant),
            price=cheapest(packages, list_price), includes_coe=coe, list_price=list_price, packages=packages,
            fuel=fuel, source_url=source_url, note=note))
    return out


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    url = pricelist_url(s.page(HOME))
    if not url:
        raise ValueError("no price list link on the MG home page")
    return parse(s.pdf(url, layout=False), source_url=url)
