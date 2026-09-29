"""Carro used car listings. The site is a JavaScript app, so pages are rendered with Chromium."""
from __future__ import annotations

import re

from scrapers.used_common import UsedScraperBase


class CarroUsedScraper(UsedScraperBase):
    name = "carro"
    # Listing pages look like /sg/en/buy/<slug>-<id> or /sg/en/car/<id>.
    listing_href = re.compile(r"/(?:buy|car|listing)/(?:[\w-]+?-)?(?P<id>\d{4,})(?:[/?#]|$)")
    needs_js = True
    labels = {
        "mileage": ("mileage",),
        "owners": ("owners", "previous owners"),
        "coe": ("coe expiry", "coe"),
        "reg_date": ("registration date", "reg date"),
        "battery": ("battery health", "battery"),
        "seller": ("sold by",),
    }
