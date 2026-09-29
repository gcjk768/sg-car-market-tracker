"""Sgcarmart used car listings."""
from __future__ import annotations

import re

from scrapers.used_common import UsedScraperBase


class SgcarmartUsedScraper(UsedScraperBase):
    name = "sgcarmart"
    # Old style info.php?ID=1234 and new style /used-cars/info/1234 links.
    listing_href = re.compile(r"(?:info\.php\?ID=|/used-cars/info/|/used_cars/info/)(?P<id>\d+)")
    needs_js = False
    labels = {
        "reg_date": ("reg date",),
        "owners": ("no. of owners", "no of owners"),
        "engine_cc": ("engine cap",),
        "coe": ("coe",),
        "seller": ("seller type", "dealer"),
        "description": ("features", "accessories", "description"),
    }
