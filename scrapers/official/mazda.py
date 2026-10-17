"""Mazda prices from mazda.com.sg (Trans Eurokars), read from its price list PDFs.

The PDFs are a template with the dates, rows and prices stamped on top. pypdf's layout mode drops the stamped
layer, so this module reads every text run with its position instead (`_runs`) and pairs a variant with the two
prices on the same line. The model label sits beside its group of rows, so a row takes the nearest label above or
below it; a row that sits about halfway between two labels is left out.

Sources: the price list linked from the home page (a dated name such as "081026 Mazda Price List.pdf", found from
the link, never built by hand) and the two undated copies under /pdf/. The linked list sits under /Upload, which
robots.txt disallows, so it is skipped; the /pdf/ copies are read but dropped once their validity date has passed.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime
from urllib.parse import quote, urljoin, urlsplit

from scrapers.base import BaseScraper, RobotsDisallowed
from scrapers.official import Blocked, OfficialPrice, OfficialScraper, cheapest

log = logging.getLogger(__name__)

BRAND = "Mazda"
HOME = "https://mazda.com.sg/"
UNDATED = ["https://mazda.com.sg/pdf/Mazda-CAT-A-Pricelist.pdf", "https://mazda.com.sg/pdf/Mazda-CAT-B-Pricelist.pdf"]

PRICE = re.compile(r"^(?:\$([\d,]+)|P\.O\.A\.)$")
DATE = re.compile(r"^\d{2}-[A-Z][a-z]{2}-\d{4}$")
GAP = 10   # points: a row this close to two model labels is ambiguous and left out


def _runs(pdf: bytes) -> str:
    """Every text run of a PDF as "page, y, x, text" lines, tab separated (the fixture format)."""
    from pypdf import PdfReader

    out = []
    for n, page in enumerate(PdfReader(io.BytesIO(pdf)).pages):
        def visit(text, cm, tm, _font, _size, n=n):
            text = text.strip()
            if text:
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                out.append(f"{n}\t{y:.0f}\t{x:.0f}\t{' / '.join(text.splitlines())}")
        page.extract_text(visitor_text=visit)
    return "\n".join(out)


def _title(label: str) -> str:
    """"MAZDA CX-5 M-HYBRID" to "Mazda CX-5 M-Hybrid": long words capitalised, codes (CX, RF, PHEV) kept."""
    return " ".join("-".join(p.capitalize() if p.isalpha() and len(p) > 4 else p for p in w.split("-"))
                    for w in label.split())


def parse(text: str, url: str = "", today: date | None = None) -> list[OfficialPrice]:
    """Rows of one price list in `_runs` format. Empty when `today` is past the list's validity."""
    runs = []
    for line in text.splitlines():
        page, y, x, t = line.split("\t", 3)
        runs.append((int(page), int(y), int(x), t))

    valid = next(((p, y) for p, y, _x, t in runs if t.startswith("Price list valid from")), None)
    dates = sorted((x, t) for p, y, x, t in runs if valid and p == valid[0] and abs(y - valid[1]) <= 5 and DATE.match(t))
    valid_from, valid_to = (dates[0][1], dates[1][1]) if len(dates) >= 2 else ("", "")
    if today and valid_to and datetime.strptime(valid_to, "%d-%b-%Y").date() < today:
        log.info("mazda: %s expired on %s", url, valid_to)
        return []
    footnote = next((t.lstrip("^") for *_, t in runs if t.startswith("^Effective Price is")), "")
    note = " ".join(s for s in (f"Price list valid {valid_from} to {valid_to}." if valid_to else "", footnote) if s)
    includes_coe = "with" if "COE Plan" in footnote else "unknown"

    rows = []
    for page, y, x, t in runs:
        if not re.match(r"^\d\.\dL ", t):
            continue
        prices = sorted((px, pt) for pp, py, px, pt in runs if pp == page and abs(py - y) <= 2 and px > x and PRICE.match(pt))
        if len(prices) != 2 or "P.O.A." in (prices[0][1], prices[1][1]):
            continue   # a template label with no prices beside it, or a price on application
        labels = sorted((abs(ly - y), ly, lx, lt) for lp, ly, lx, lt in runs
                        if lp == page and lt.startswith("MAZDA ") and "SHOWROOM" not in lt and lx < x)
        if not labels or (len(labels) > 1 and labels[1][0] - labels[0][0] < GAP):
            log.info("mazda: no certain model for %r at y=%s, left out", t, y)
            continue
        _d, ly, lx, label = labels[0]
        sub = [st for sp, sy, sx, st in runs if sp == page and 0 < ly - sy <= 15 and abs(sx - lx) < 40
               and re.fullmatch(r"[A-Z][A-Z -]+", st) and not st.startswith(("MAZDA", "VES"))]
        model = _title(" ".join([label, *sub]))
        list_price, effective = (int(p[1].strip("$").replace(",", "")) for p in prices)
        packages = {"Effective price (8 bid non guaranteed COE plan)": effective} if footnote else {}
        rows.append(OfficialPrice(
            make=BRAND, model=model, variant=t, price=cheapest(packages, list_price), includes_coe=includes_coe,
            list_price=list_price, packages=packages,
            fuel="hybrid" if re.search(r"HYBRID|PHEV", model, re.I) else "", source_url=url, note=note))
    return rows


def _pdf_runs(s: OfficialScraper, url: str) -> str:
    """Like OfficialScraper.pdf (robots, throttle, day cache) but in `_runs` format. Empty for a missing file."""
    key = url + "#runs"
    cached = s._read_cache(key)
    if cached is not None:
        return cached
    if not s._allowed(url):
        raise RobotsDisallowed(url)
    BaseScraper._throttle.wait(urlsplit(url).netloc)
    log.info("GET %s", url)
    resp = s.client.get(quote(url, safe=":/?=&%"))
    if resp.status_code in (403, 429):
        raise Blocked(url)
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    text = _runs(resp.content)
    s._write_cache(key, text)
    return text


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    home = s.page(HOME)
    linked = [urljoin(HOME, h) for h in re.findall(r'href="([^"]+\.pdf)"', home, re.I) if "price" in h.lower()]
    rows: list[OfficialPrice] = []
    disallowed = []
    for url in dict.fromkeys(linked + UNDATED):
        try:
            rows += parse(_pdf_runs(s, url), url, s.run_date)
        except RobotsDisallowed:
            disallowed.append(url)
            log.info("mazda: robots.txt disallows %s, skipped", url)
    if not rows and disallowed:
        raise RobotsDisallowed(disallowed[0])   # the current list is off limits and the readable copies are stale
    return rows
