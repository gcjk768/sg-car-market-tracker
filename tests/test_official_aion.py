"""GAC Aion: the site has no readable prices, so the module must report blocked without fetching anything."""
import re
from pathlib import Path

import pytest

from scrapers.official import Blocked, aion, brand_modules

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "aion_v_shell.html"


class _NoFetch:
    """Fails the test on any helper call, so scrape() can never reach the site or its /api/*."""

    def __getattr__(self, name):
        raise AssertionError(f"aion.scrape() called s.{name}()")


def test_module_is_registered():
    assert aion.BRAND == "GAC Aion"
    assert aion in brand_modules()


def test_fixture_is_the_empty_shell():
    html = FIXTURE.read_text(encoding="utf-8")
    assert '<div id="__next"><div class="h-full w-full"></div></div>' in html
    assert not re.search(r"\$\s?\d{2,3},\d{3}", html)   # no price text: if this fails, the site now prints prices
    assert aion.parse(html) == []


def test_scrape_reports_blocked_without_fetching():
    with pytest.raises(Blocked, match="robots.txt"):
        aion.scrape(_NoFetch())
