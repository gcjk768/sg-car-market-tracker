"""Citroen official price list parsed from saved live copies (2026-10-17): the PDF's layout text and the home page."""
from datetime import date
from pathlib import Path

from scrapers.official import citroen

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "official"


def test_parse_citroen_price_list():
    text = (FIXTURES / "citroen_pricelist_pdf.txt").read_text(encoding="utf-8")
    rows = citroen.parse(text)
    assert len(rows) == 1   # the list (8 to 21 Oct 2026) carries only the ë-C4X Shine Plus
    assert {(r.model, r.variant, r.price) for r in rows} == {("ë-C4X", "Shine Plus", 98999)}
    r = rows[0]
    assert r.make == "Citroen" and r.includes_coe == "without" and r.fuel == "ev"
    assert r.list_price == 98999 and r.packages == {}
    assert r.note.startswith("Effective 8 Oct 2026, 8:30am to 21 Oct 2026, 12:00pm.")
    assert "$74,999" in r.note
    assert citroen.valid_until(text) == date(2026, 10, 21)


def test_price_list_link_comes_from_the_home_page():
    html = (FIXTURES / "citroen_home.html").read_text(encoding="utf-8")
    assert citroen.price_list_link(html) == "https://cyclecarriage.short.gy/citroen-price-list"
    assert citroen.price_list_link("<html></html>") == citroen.SHORT_LINK
