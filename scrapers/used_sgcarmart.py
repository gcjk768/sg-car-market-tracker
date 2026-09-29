"""Sgcarmart used car listings."""
from __future__ import annotations

import re

from scrapers.used_common import UsedScraperBase


class SgcarmartUsedScraper(UsedScraperBase):
    name = "sgcarmart"
    # Old style info.php?ID=1234, /used-cars/info/1234 and, since the 2026 redesign,
    # /used-cars/info/byd-atto-3-electric-1539662/.
    listing_href = re.compile(r"(?:info\.php\?ID=|/used-cars/info/|/used_cars/info/)(?:[a-z0-9-]*?-)?(?P<id>\d{5,})/?(?:[?#\"]|$)")
    # The redesigned results page draws its listings with JavaScript. Detail pages do not.
    needs_js = True

    def _get(self, url: str) -> str:
        return self.fetch_rendered(url) if "/listing" in url else self.fetch(url)
    labels = {
        "reg_date": ("reg date",),
        "owners": ("no. of owners", "no of owners"),
        "engine_cc": ("engine cap",),
        "coe": ("coe",),
        "seller": ("seller type", "dealer"),
        "description": ("features", "accessories", "description"),
    }
