"""Kia: Cycle & Carriage's two page price list PDF (hybrid page, electric page).

The PDF name carries its start date (KiaRangePricelist_08Oct26.pdf) and changes every two weeks. Kia's own
"request a pricelist" page is a one line script redirect to the current file, so scrape() reads the link from there.

Layout text puts each price row on one line, but the model name (a merged cell) and wrapped variant words sit on
nearby lines. Rows are grouped by runs of two or more blank lines and each group must carry exactly one model name;
wrapped variant words go to the nearest price row. Anything that cannot be paired for certain is left out.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from scrapers.official import OfficialPrice, OfficialScraper, cheapest

log = logging.getLogger(__name__)

BRAND = "Kia"
PRICELIST_PAGE = "https://www.kia.com/sg/shopping-tools/request-a-pricelist.html"

_FRAG = re.compile(r"\S+(?: \S+)*")                       # text runs split by two or more spaces
_ROW = re.compile(r"(?<!\S)(A|B|C1|C2|C3)\s+\d+(?:\.\d+)?\s+\$([\d,]+)(.*)")   # VES band, consumption, list price
_MONEY = re.compile(r"-\s?\$[\d,]+|(?<!\S)-(?!\S)|\$[\d,]+")   # a lone "-" is an empty discount cell
_SECTION = re.compile(r"Category\s+([A-E])\s+COE")
_END = re.compile(r"Promotions|VES\s+rebate|Price\s+List|Category\s+[A-E]\s+COE")
_PAGE = re.compile(r"Kia\s+(Hybrid|Electric)\s+Price\s+List")
_VALID = re.compile(r"Valid\s+from\s+(\d{1,2})\s*\W\s*(\d{1,2}\s+\w+\s+\d{4})")
_SEATS = re.compile(r"^\d+-Seater$")
_TAG = re.compile(r"Time\s*only|Limited")


def find_pdf(html: str, base: str = PRICELIST_PAGE) -> str:
    """The current price list PDF linked (or script redirected) from Kia's pricelist page."""
    m = re.search(r"[^\"'\s]*Pric\w*list[^\"'\s]*\.pdf", html, re.I)
    if not m:
        raise ValueError("no price list PDF linked on " + base)
    return urljoin(base, m.group(0))


def parse(text: str, source_url: str = "") -> list[OfficialPrice]:
    lines = text.splitlines()
    out: list[OfficialPrice] = []
    fuel, valid = "", ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if m := _PAGE.search(line):
            fuel = "hybrid" if m.group(1) == "Hybrid" else "ev"
        if m := _VALID.search(line):
            valid = f"{m.group(1)} to {m.group(2)}"
        m = _SECTION.search(line)
        if not m:
            i += 1
            continue
        end = i + 1
        while end < len(lines) and not _END.search(lines[end]):
            end += 1
        out += _section(lines[i + 1:end], m.group(1), fuel, valid, source_url)
        i = end
    return out


def _section(lines: list[str], cat: str, fuel: str, valid: str, source_url: str) -> list[OfficialPrice]:
    rows = [n for n, l in enumerate(lines) if _ROW.search(l)]
    if not rows:
        return []
    header = lines[:rows[0]]
    hl = next(l for l in header if "Models" in l and "Variant" in l)
    cutoff = (hl.index("Models") + len("Models") + hl.index("Variant")) // 2   # model column ends about here
    ves_x = min(_ROW.search(lines[n]).start() for n in rows)
    names = sorted(((l.find(w), w) for l in header for w in ("Platinum", "Gold", "Standard") if w in l))
    pkg_names = [w for _, w in names]
    hdr = " ".join(header)   # "List Price Inclusive of COE & VES", wrapped over two or three lines
    includes = "with" if "Inclusive" in hdr and "COE" in hdr else "unknown"

    # Groups: lines from the first price row, split at two or more blank lines.
    groups, cur, blanks = [], [], 0
    for n in range(rows[0], len(lines)):
        if not lines[n].strip():
            blanks += 1
            continue
        if blanks >= 2 and cur:
            groups.append(cur)
            cur = []
        blanks = 0
        cur.append(n)
    if cur:
        groups.append(cur)
    groups = [g for g in groups if any(n in rows for n in g)]

    def model_frags(g):
        return [f.group(0) for n in g for f in _FRAG.finditer(lines[n]) if f.start() < cutoff]

    def labelled(g):
        return any(not _SEATS.match(f) and f != "NEW" for f in model_frags(g))

    # A group without a model name (a variant cell wrapped across a wide gap) joins its only neighbour.
    if len(groups) == 2 and labelled(groups[0]) != labelled(groups[1]):
        groups = [groups[0] + groups[1]]

    out = []
    for g in groups:
        frags = model_frags(g)
        models = {f for f in frags if not _SEATS.match(f) and f != "NEW"}   # "NEW" is a badge
        seats = " ".join(dict.fromkeys(f for f in frags if _SEATS.match(f)))
        grows = [n for n in g if n in rows]
        if len(models) != 1:
            log.warning("kia: cannot name the model for rows %s", [lines[n].strip()[:40] for n in grows])
            continue
        model = models.pop()
        words: dict[int, list[tuple[int, int, str]]] = {n: [] for n in grows}
        unsure: set[int] = set()
        for n in g:
            for f in _FRAG.finditer(lines[n]):
                if not cutoff <= f.start() < ves_x:
                    continue
                dist = sorted((abs(n - r), r) for r in grows)
                if len(dist) > 1 and dist[0][0] == dist[1][0]:
                    unsure.update((dist[0][1], dist[1][1]))
                words[dist[0][1]].append((n, f.start(), f.group(0)))
        for r in grows:
            if r in unsure:
                log.warning("kia: variant words for %r sit between two rows, left out", lines[r].strip()[:60])
                continue
            raw = " ".join(w for _, _, w in sorted(words[r]))
            limited = bool(_TAG.search(raw))
            variant = re.sub(r"\s+-(?=[A-Za-z])", "-", " ".join(_TAG.sub(" ", raw).split()))
            variant = " ".join(p for p in (variant, seats, "(Limited Time only)" if limited else "") if p)
            m = _ROW.search(lines[r])
            list_price = int(m.group(2).replace(",", ""))
            pkgs = [int(t[1:].replace(",", "")) for t in _MONEY.findall(m.group(3)) if t.startswith("$")]
            note = f"Valid from {valid}. COE Category {cat}." if valid else f"COE Category {cat}."
            packages: dict[str, int] = {}
            row_coe = includes
            body_only = any("Without COE" in lines[n] or "Body Only" in lines[n] for n in g)
            if len(pkgs) == len(pkg_names):
                packages = dict(zip(pkg_names, pkgs))
            elif len(pkgs) == 1 and body_only:
                # EV9 Body Only: list price minus all discounts, printed once and marked "(Without COE)"
                packages = {"Package (without COE)": pkgs[0]}
                row_coe = "without"
                note += " Body Only, sold without COE. The list price column says Inclusive of COE."
            elif pkgs:
                note += " Also printed: " + ", ".join(f"${p:,}" for p in pkgs) + "."
            out.append(OfficialPrice(make=BRAND, model=model, variant=variant, price=cheapest(packages, list_price),
                                     includes_coe=row_coe, list_price=list_price, packages=packages, fuel=fuel,
                                     source_url=source_url, note=note))
    return out


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    url = find_pdf(s.page(PRICELIST_PAGE))
    return parse(s.pdf(url), url)
