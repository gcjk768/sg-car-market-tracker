"""Zeekr Singapore (zeekrlife.com/en-sg): each model page, read as plain HTML.

Every page section ships its data as JSON in a `data-component-props` attribute. A model page with prices has one
titled "Choose the Zeekr 7X that fits your style" whose `models` list holds each variant's `title` and `price`
("$ 279,999"), and whose `declaration` says "Price valid till <date>.". The page does not say whether COE is
included, so includes_coe stays "unknown". Today only the 7X page shows prices (X and 009 do not), but every model
linked from the menu is read so a new price section is picked up without a code change.
shop.zeekr.sg is not read: its robots.txt disallows everything.
"""
from __future__ import annotations

import html
import json
import logging
import re

from scrapers.base import FetchError, RobotsDisallowed
from scrapers.official import Blocked, OfficialPrice, OfficialScraper

log = logging.getLogger(__name__)

BRAND = "Zeekr"
SITE = "https://www.zeekrlife.com"
URL = SITE + "/en-sg/models/7x/"   # without the slash the site answers with a redirect

_PROPS = re.compile(r'data-component-props="([^"]*)"')
_SECTION = re.compile(r"^Choose the Zeekr (?P<model>\S+) that fits your style")
_VALID = re.compile(r"Price valid till [^.]+\.")
_MODEL_LINK = re.compile(r'href="(/en-sg/models/[a-z0-9]+)/?"')


def parse(text: str, source_url: str = URL) -> list[OfficialPrice]:
    rows: list[OfficialPrice] = []
    for raw in _PROPS.findall(text):
        props_text = html.unescape(raw)
        if "fits your style" not in props_text:
            continue
        props = json.loads(props_text)
        sec = _SECTION.match(props.get("title") or "")
        if not sec:
            continue
        valid = _VALID.search(props.get("declaration") or "")
        fuel = "ev" if "Electric Motor" in props_text else ""
        rows += [OfficialPrice(make=BRAND, model=sec["model"], variant=m["title"].strip(),
                               price=int(re.sub(r"\D", "", m["price"])), fuel=fuel, source_url=source_url,
                               note=valid.group(0) if valid else "")
                 for m in props.get("models") or [] if re.search(r"\d", m.get("price") or "")]
    return rows


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    first = s.page(URL)   # raises Blocked on a challenge page
    rows = parse(first)
    for path in dict.fromkeys(_MODEL_LINK.findall(first)):
        url = SITE + path + "/"
        if url == URL:
            continue
        try:
            rows += parse(s.page(url), url)
        except (Blocked, FetchError, RobotsDisallowed) as exc:   # keep the 7X rows; stop, never retry around a limit
            log.warning("zeekr %s skipped: %s", url, exc)
            break
    return rows
