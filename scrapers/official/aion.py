"""GAC Aion Singapore (aionev.com.sg): no price can be read today, so the brand reports itself blocked.

The model pages (/models/v, /models/y-plus, /models/es, /models/ut, /models/e9, /models/hyptec-ht) and /deals
are a Next.js shell: the HTML carries no price text (see fixtures/official/aion_v_shell.html) and the page script
loads every figure from /api/*, which robots.txt disallows. Jina Reader refuses the domain. A headless render
would make those /api/* calls for us, so it is not used either. Sgcarmart covers Aion until the site prints
prices in its HTML or the owner pastes them.
"""
from __future__ import annotations

from scrapers.official import Blocked, OfficialPrice, OfficialScraper

BRAND = "GAC Aion"
URL = "https://aionev.com.sg/models/v"
REASON = "prices load from /api/*, which robots.txt disallows; the HTML shell has none"


def parse(html: str) -> list[OfficialPrice]:
    """The page HTML carries no prices, so there is nothing to pair. Kept for the brand module contract."""
    return []


def scrape(s: OfficialScraper) -> list[OfficialPrice]:
    # No request at all: a fetch would only return the empty shell again.
    raise Blocked(f"{URL}: {REASON}")
