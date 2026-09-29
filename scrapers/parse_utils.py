"""Small parsing helpers shared by every scraper.

The sites change layout without notice, so parsers lean on labels and number patterns rather
than exact CSS paths wherever they can.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable, Optional

from selectolax.parser import HTMLParser, Node

_WS = re.compile(r"\s+")
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_DATE_FORMATS = (
    "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%b %Y", "%B %Y", "%d-%b-%y",
)


def clean(text: str | None) -> str:
    return _WS.sub(" ", text or "").replace("\xa0", " ").strip()


def text_of(node: Node | None) -> str:
    if node is None:
        return ""
    return clean(node.text(separator=" "))


def parse_number(text: str | None) -> Optional[float]:
    """First number in the text, commas allowed. None when there is none."""
    if not text:
        return None
    m = _NUM.search(text.replace("S$", "$"))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_int(text: str | None) -> Optional[int]:
    n = parse_number(text)
    return int(round(n)) if n is not None else None


def parse_money(text: str | None) -> Optional[int]:
    """Money in SGD as an integer. Handles $131,890, S$ 131,890 and 131890. 'k' suffix is expanded."""
    if not text:
        return None
    t = text.replace("S$", "$")
    m = re.search(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?", t)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    if m.group(2):
        value *= 1000
    return int(round(value))


def parse_km(text: str | None) -> Optional[int]:
    """Mileage in km. Accepts '28,000 km', '28k km' and plain numbers."""
    if not text:
        return None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?\s*km", text)
    if m:
        v = float(m.group(1).replace(",", ""))
        return int(round(v * 1000 if m.group(2) else v))
    return parse_int(text)


def parse_date(text: str | None) -> Optional[date]:
    """Parse the first date like token in text. Returns None if nothing matches."""
    if not text:
        return None
    t = clean(text)
    candidates = re.findall(r"\d{1,2}[-/ ]\w{3,9}[-/ ]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\w{3,9} \d{4}", t)
    for c in candidates + [t]:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(c, fmt).date()
            except ValueError:
                continue
    return None


def parse_years(text: str | None) -> Optional[float]:
    """Years from strings like '7 yrs 3 mths', '6.5 years' or '8 years'."""
    if not text:
        return None
    years = re.search(r"(\d+(?:\.\d+)?)\s*(?:yrs?|years?)", text, re.I)
    months = re.search(r"(\d+)\s*(?:mths?|months?)", text, re.I)
    if not years and not months:
        return None
    total = float(years.group(1)) if years else 0.0
    if months:
        total += int(months.group(1)) / 12
    return round(total, 2)


def tables(html: str | HTMLParser) -> list[tuple[list[str], list[list[str]]]]:
    """All tables as (headers, rows) of cleaned text. Header is the first row with th cells,
    or the first row when there are no th cells."""
    tree = html if isinstance(html, HTMLParser) else HTMLParser(html)
    out = []
    for table in tree.css("table"):
        rows = []
        for tr in table.css("tr"):
            cells = [text_of(c) for c in tr.css("th, td")]
            if cells:
                rows.append(cells)
        if not rows:
            continue
        header_idx = 0
        for i, tr in enumerate(table.css("tr")):
            if tr.css("th"):
                header_idx = i
                break
        headers = rows[header_idx] if header_idx < len(rows) else rows[0]
        body = rows[header_idx + 1 :]
        out.append((headers, body))
    return out


def label_values(html: str | HTMLParser) -> dict[str, str]:
    """Collect label to value pairs from two cell table rows, dt/dd pairs and 'Label: value' lines.

    Keys are lower cased and stripped of trailing colons. Later matches do not overwrite earlier
    ones, so the most prominent block on the page wins.
    """
    tree = html if isinstance(html, HTMLParser) else HTMLParser(html)
    found: dict[str, str] = {}

    def put(label: str, value: str) -> None:
        key = clean(label).rstrip(":").lower()
        value = clean(value)
        if key and value and key not in found:
            found[key] = value

    for tr in tree.css("tr"):
        cells = tr.css("th, td")
        if len(cells) == 2:
            put(text_of(cells[0]), text_of(cells[1]))
    for dl in tree.css("dl"):
        dts, dds = dl.css("dt"), dl.css("dd")
        for dt, dd in zip(dts, dds):
            put(text_of(dt), text_of(dd))
    # Label and value as the only two text children of one row, e.g. Motorist's
    # <div><i/><span>Registration Date</span><span>15/06/2022</span></div>.
    for row in tree.css("div, li"):
        kids = [c for c in row.iter() if c.tag != "-text" and text_of(c)]
        if len(kids) == 2 and not any(True for _ in kids[0].iter()) and len(text_of(kids[0])) < 40:
            put(text_of(kids[0]), text_of(kids[1]))
    block_tags = {"li", "p", "div", "table", "ul", "ol", "section", "article", "dl"}
    for node in tree.css("li, p, div, span"):
        if any(child.tag in block_tags for child in node.iter()):
            continue
        txt = text_of(node)
        if ":" in txt and len(txt) < 120:
            label, _, value = txt.partition(":")
            put(label, value)
    return found


def first_match(mapping: dict[str, str], *needles: str) -> Optional[str]:
    """Value of the first key that contains any needle (all lower case)."""
    for needle in needles:
        for key, value in mapping.items():
            if needle in key:
                return value
    return None


def contains_any(text: str, keywords: Iterable[str]) -> list[str]:
    low = text.lower()
    return [k for k in keywords if k.lower() in low]
