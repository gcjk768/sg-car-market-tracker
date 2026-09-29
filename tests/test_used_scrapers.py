from datetime import date
from pathlib import Path

from models import Drivetrain
from scrapers.used_carro import CarroUsedScraper
from scrapers.used_common import detect_drivetrain, split_make_model
from scrapers.used_motorist import MotoristUsedScraper
from scrapers.used_sgcarmart import SgcarmartUsedScraper

FIX = Path(__file__).resolve().parent.parent / "fixtures"
RUN = date(2026, 9, 29)


def test_split_make_model():
    assert split_make_model("BYD Atto 3 Electric Dynamic 60.5kWh") == ("BYD", "Atto 3", "Electric Dynamic 60.5kWh")
    assert split_make_model("Tesla Model 3 Standard Range Plus") == ("Tesla", "Model 3", "Standard Range Plus")
    assert split_make_model("Mercedes-Benz A200 AMG Line") == ("Mercedes-Benz", "A200", "AMG Line")
    assert split_make_model("GAC Aion Y Plus Premium") == ("GAC Aion", "Y", "Plus Premium")


def test_detect_drivetrain():
    assert detect_drivetrain("Fuel Type Electric", Drivetrain.ice) == Drivetrain.ev
    assert detect_drivetrain("Petrol-Electric hybrid", Drivetrain.ice) == Drivetrain.hybrid
    assert detect_drivetrain("Petrol", Drivetrain.ice) == Drivetrain.ice


def test_sgcarmart_list_and_detail(cfg):
    s = SgcarmartUsedScraper(cfg, "ua", RUN, group="ev")
    cards = s.parse((FIX / "sgcarmart_used_list.html").read_text())
    assert [c["listing_id"] for c in cards] == ["1401001", "1401002", "1401003"]
    assert cards[0]["price"] == 118800
    assert cards[0]["url"].startswith("https://www.sgcarmart.com/")
    listing = s.parse_detail((FIX / "sgcarmart_used_detail.html").read_text(), cards[0])
    assert listing.make == "BYD" and listing.model == "Atto 3"
    assert listing.price == 118800 and listing.depreciation_per_year == 11900
    assert listing.reg_date == date(2023, 3, 12) and listing.year == 2023
    assert listing.mileage_km == 28000 and listing.owners == 1
    assert listing.coe_years_remaining == 7.42
    assert listing.omv == 31842 and listing.arf == 28079
    assert listing.power_kw == 150 and listing.engine_cc is None
    assert listing.drivetrain == Drivetrain.ev
    assert listing.seller_type == "dealer"
    assert "battery health report 97%" in listing.description
    assert listing.flags == []


def test_carro_list_and_detail(cfg):
    s = CarroUsedScraper(cfg, "ua", RUN, group="ice")
    cards = s.parse((FIX / "carro_used_list.html").read_text())
    assert [c["listing_id"] for c in cards] == ["2201001", "2201002"]
    assert cards[1]["price"] == 112800
    listing = s.parse_detail((FIX / "carro_used_detail.html").read_text(), cards[0])
    assert listing.drivetrain == Drivetrain.hybrid
    assert listing.engine_cc == 1598 and listing.coe_expiry == date(2033, 6, 17)
    assert 6.6 < listing.coe_years_remaining < 6.8
    assert listing.depreciation_per_year == 11200
    assert listing.seller_type == "dealer"


def test_motorist_list_and_detail(cfg):
    s = MotoristUsedScraper(cfg, "ua", RUN, group="ev")
    cards = s.parse((FIX / "motorist_used_list.html").read_text())
    assert [c["listing_id"] for c in cards] == ["57373", "57873"]
    listing = s.parse_detail((FIX / "motorist_used_detail.html").read_text(), cards[1])
    assert listing.make == "GAC Aion" and listing.price == 99988
    assert listing.reg_date == date(2024, 5, 3)
    assert listing.coe_expiry == date(2034, 5, 2)
    assert listing.seller_type == "direct owner"
    assert listing.power_kw == 100
    assert "as is" in listing.flags and "no warranty" in listing.flags


def test_bare_href_is_skipped_not_a_crash(cfg):
    # Live Sgcarmart pages carry <a href> with no value, which selectolax returns as None.
    html = "<a href>menu</a>" + (FIX / "sgcarmart_used_list.html").read_text()
    s = SgcarmartUsedScraper(cfg, "ua", RUN, group="ev")
    assert len(s.parse(html)) == 3


def test_motorist_live_detail_page(cfg):
    # Saved from motorist.sg on 2026-09-29: label and value are sibling spans, the price has
    # no label, and the site menu says "Scrap / Export".
    s = MotoristUsedScraper(cfg, "ua", RUN, group="ev")
    card = {"listing_id": "53696", "url": "https://www.motorist.sg/used-car/53696/x", "title": "", "price": None}
    listing = s.parse_detail((FIX / "motorist_used_detail_live.html").read_text(encoding="utf-8"), card)
    assert (listing.price, listing.depreciation_per_year) == (136800, 18681)
    assert (listing.reg_date, listing.owners, listing.omv) == (date(2022, 6, 15), 1, 73984)
    assert listing.coe_expiry == date(2032, 6, 14)
    assert listing.flags == []


def test_throttle_honours_crawl_delay(monkeypatch):
    from scrapers.base import _Throttle
    import scrapers.base as base

    slept = []
    monkeypatch.setattr(base.time, "sleep", slept.append)
    t = _Throttle(2.0, 0.0)
    t.min_gap["www.sgcarmart.com"] = 30.0
    t.wait("www.sgcarmart.com")
    t.wait("www.sgcarmart.com")
    assert slept and slept[0] > 29


def test_coe_left_short_and_long_forms():
    from scrapers.used_common import _coe_left_years

    assert _coe_left_years("7yrs 10mths 24days COE left") == 7.83
    assert round(_coe_left_years("1y 1m COE left"), 2) == 1.08
