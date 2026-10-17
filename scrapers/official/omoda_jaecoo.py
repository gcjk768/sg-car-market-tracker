"""Omoda and Jaecoo Singapore (one shared distributor site): the COE package prices on each model page.

Every model page has a "COE Price Update" block: the COE category, its date and premium, then a "Non-Guaranteed"
and a "Guaranteed" price. There is one trim per model, so each page gives one row whose packages are those two
prices. The site calls them COE packages, so includes_coe is "with". The model list comes from the menu links on
the Jaecoo landing page, which links the Omoda models too.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from scrapers.official import OfficialPrice, OfficialScraper, cheapest

BRAND = "Omoda / Jaecoo"
SITE = "https://omodajaecoo.com.sg"
INDEX = SITE + "/jaecoo/"

_LINK = re.compile(r'href="(?:https://omodajaecoo\.com\.sg)?/(omoda|jaecoo)/([a-z0-9-]+)/?"')
_BLOCK = re.compile(r"COE Price Update\ncat ([ab]) coe\n(.+?)\n\$([\d,]+)\n"
                    r"Non-Guaranteed\n\$([\d,]+)\nGuaranteed\n\$([\d,]+)", re.I)
_VALID = re.compile(r"valid until ([^.\n]+)", re.I)


def model_urls(index_html: str) -> list[str]:
    """Every /omoda/<model>/ and /jaecoo/<model>/ page the menu links."""
    return sorted({f"{SITE}/{brand}/{slug}/" for brand, slug in _LINK.findall(index_html)})


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def parse(page: str, source_url: str) -> list[OfficialPrice]:
    """One row from a model page, or none when the page has no price block (the brand DNA page)."""
    tree = HTMLParser(page)
    title = tree.css_first("title").text() if tree.css_first("title") else ""
    for node in tree.css("script, style"):
        node.decompose()
    text = "\n".join(l.strip() for l in tree.body.text(separator="\n").splitlines() if l.strip())
    m = _BLOCK.search(text)
    if not m:
        return []
    cat, coe_date, premium, non_gtd, gtd = m.groups()
    # The title is "<make> <model>", then the site name. Sgcarmart stores the model without the make ("5", "E5")
    # and a one trim model has no variant name, so the row does the same for official.match() to pair it.
    make, _, model = re.split(r"\s+[\u2013|]\s+", title)[0].strip().partition(" ")
    packages = {"Non-Guaranteed COE": _num(non_gtd), "Guaranteed COE": _num(gtd)}
    # A plug-in hybrid lists an electric only range next to the combined one; a pure EV lists only a battery.
    fuel = "hybrid" if "WLTP-Electric" in text else "ev" if "Battery Capacity" in text else ""
    valid = _VALID.search(text[m.end():])
    note = f"Cat {cat.upper()} COE ${premium} on {coe_date}"
    if valid:
        note += f". Promotion valid until {valid.group(1).strip()}"
    return [OfficialPrice(
        make=make, model=model, variant="", price=cheapest(packages, 0), includes_coe="with",
        packages=packages, fuel=fuel, source_url=source_url, note=note)]


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    rows = []
    for url in model_urls(s.page(INDEX)):
        rows.extend(parse(s.page(url), url))
    if not rows:
        raise ValueError("no COE Price Update block on any model page")
    return rows
