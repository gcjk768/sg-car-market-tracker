from pathlib import Path

import pytest

from scrapers.official import mini

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "mini_all-electric-mini-cooper.html"


def test_rendered_mini_page_has_no_prices():
    html = FIX.read_text(encoding="utf-8")
    assert "Variants &amp; Prices" in html and "fsm_tp_product_cards" in html   # the right page, cards component present
    assert mini.parse(html) == []


def test_parse_flags_prices_once_they_appear():
    with pytest.raises(ValueError):
        mini.parse("<p>MINI Cooper E Classic S$ 189,888</p>")


def test_scrape_returns_nothing():
    assert mini.scrape(None) == []
