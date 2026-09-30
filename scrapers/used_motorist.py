"""Motorist.sg used car listings."""
from __future__ import annotations

import re

from scrapers.used_common import UsedScraperBase


class MotoristUsedScraper(UsedScraperBase):
    name = "motorist"
    # Listing pages look like /used-cars/<slug>/<id> or /used-car/<id>.
    listing_href = re.compile(r"/used-cars?/(?:[\w-]+/)?(?P<id>\d{4,})(?:[/?#]|$)")
    needs_js = False
    sold_marker = re.compile(r"badge-danger[^>]*>\s*Sold\s*<")
    labels = {
        "reg_date": ("registration date", "reg date"),
        "owners": ("no. of owners", "owners"),
        "coe": ("coe expiry", "coe"),
        "seller": ("listed by", "seller"),
    }
