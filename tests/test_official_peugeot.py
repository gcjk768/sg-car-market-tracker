from datetime import date
from pathlib import Path

import pytest

from scrapers.base import FetchError
from scrapers.official import peugeot

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "official"
PDF_TEXT = (FIX / "peugeot_pricelist_pdf.txt").read_text(encoding="utf-8")
HTML = (FIX / "peugeot_pricelist.html").read_text(encoding="utf-8")
PDF_URL = "https://www.peugeot.sg/content/dam/peugeot/singapore/pricelist/AP2502_Peugeot_Price_list_6Feb-19Feb_2025.pdf"


def test_pdf_link_found_on_page():
    assert peugeot.pdf_link(HTML) == PDF_URL


def test_parse_rows_checked_against_pdf():
    rows = peugeot.parse(PDF_TEXT, PDF_URL)
    got = {(r.make, r.model, r.variant, r.list_price) for r in rows}
    assert len(rows) == 2
    assert got == {("Peugeot", "408", "1.2 Allure Premium Fastback", 184999),
                   ("Peugeot", "408", "1.2 GT Fastback", 200999)}
    allure = next(r for r in rows if r.variant == "1.2 Allure Premium Fastback")
    assert allure.price == 184999
    assert allure.includes_coe == "with"
    assert allure.packages == {"Non-Guaranteed COE": 184999, "Guaranteed COE": 188999}   # $4,000 guaranteed top up
    assert allure.note == "Effective 6 Feb 2025, 8:30am to 19 Feb 2025, 12pm"
    assert allure.fuel == ""
    gt = next(r for r in rows if r.variant == "1.2 GT Fastback")
    assert (gt.price, gt.packages["Guaranteed COE"]) == (200999, 204999)


def test_expired_list_is_reported_not_returned():
    assert peugeot.valid_until(PDF_TEXT) == date(2025, 2, 19)

    class Stub:
        run_date = date(2026, 10, 17)
        page = staticmethod(lambda url: HTML)
        pdf = staticmethod(lambda url: PDF_TEXT)

    with pytest.raises(FetchError, match="expired on 2025-02-19"):
        peugeot.scrape(Stub())
    Stub.run_date = date(2025, 2, 10)
    assert len(peugeot.scrape(Stub())) == 2
