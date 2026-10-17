"""Sgcarmart's full new car listing: the React payload parsed from a trimmed live page (2026-10-17)."""
from pathlib import Path

import pytest

from scrapers.new_cars import SgcarmartNewCarScraper, engine_cc_estimate, fuel_of, parse_search_list, parse_total_pages

PAGE = (Path(__file__).resolve().parent.parent / "fixtures" / "sgcarmart_new_listing.html").read_text(encoding="utf-8")


def test_payload_has_fifteen_models_and_the_page_count():
    models = parse_search_list(PAGE)
    assert len(models) == 15 and models[0]["make"] == "Toyota" and parse_total_pages(PAGE) == 38
    assert parse_search_list("<html>nothing</html>") == []


def test_variants_carry_fuel_power_engine_and_price_moves(cfg):
    v = SgcarmartNewCarScraper(cfg, "ua").parse(PAGE)
    noah = next(x for x in v if x.model == "Noah Hybrid MYCAR Edition")
    assert (noah.fuel, noah.price_with_coe, noah.engine_cc, noah.fuel_economy) == ("hybrid", 225000, 1800, "23.4km/L")
    assert noah.power_kw == round(138 * 0.7457, 1)
    sealion = next(x for x in v if x.model == "Sealion 7 Electric" and x.variant == "Dynamic 71.8kWh")
    assert sealion.fuel == "ev" and sealion.price_change == -2000 and sealion.price_change_on == "2026-10-09"
    assert sealion.listing_url == "https://www.sgcarmart.com/new-cars/info/21873/byd-sealion-7-electric"


def test_goods_vehicles_and_truck_makes_are_dropped(cfg):
    old = '\\"make\\":\\"Toyota\\",\\"model\\":\\"Noah Hybrid MYCAR Edition\\"'
    assert old in PAGE
    page = PAGE.replace(old, '\\"make\\":\\"Toyota\\",\\"model\\":\\"Hiace Van\\"')
    assert not any("Hiace" in x.model for x in SgcarmartNewCarScraper(cfg, "ua").parse(page))
    cfg["new_cars"]["exclude_makes"] = ["BYD"]
    assert not any(x.make == "BYD" for x in SgcarmartNewCarScraper(cfg, "ua").parse(PAGE))


def test_fuel_and_engine_helpers():
    assert fuel_of("byd-seal-electric", "5km/kWh") == "ev" and fuel_of("toyota-camry-hybrid", "20km/L") == "hybrid"
    assert fuel_of("isuzu-d-max-diesel", None) == "diesel" and fuel_of("honda-civic", "16km/L") == "petrol"
    assert engine_cc_estimate("1.5 Turbo Sport") == 1500 and engine_cc_estimate("Elegance", "Corolla 1.8") == 1800
    assert engine_cc_estimate("Model 3 RWD") is None and engine_cc_estimate("v12.5 trim") is None


def test_popularity_is_listing_order_without_spotlight_or_repeats(cfg):
    scraper = SgcarmartNewCarScraper(cfg, "ua")
    v = scraper.parse(PAGE)
    rank = {x.model: x.popularity for x in v}
    # The live page opens with a paid spotlight slot (Toyota Noah MYCAR Edition): it never sets a rank.
    assert rank["Noah Hybrid MYCAR Edition"] is None
    assert rank["Sealion 7 Electric"] == 1 and rank["Sealion 8 Plug-in Hybrid"] == 2 and rank["Model Y Electric"] == 3

    def model(i, name, spotlight=False):
        return {"id": i, "make": "X", "model": name, "slug": name.lower(), "additionalProperties": {"isSpotlight": spotlight},
                "subModels": [{"name": "base", "price": 100000 + i}]}
    # page 1 carries an advert for C, which is ranked where it really sits on page 3; every model counts once
    listing = [model(3, "C", spotlight=True), model(1, "A"), model(2, "B"), model(1, "A"), model(3, "C"), model(4, "D")]
    got = {x.model: x.popularity for x in scraper.variants(listing)}
    assert got == {"A": 1, "B": 2, "C": 3, "D": 4}
    assert len(scraper.variants(listing)) == 4


def test_listing_says_which_models_have_a_360_view(cfg):
    v = SgcarmartNewCarScraper(cfg, "ua").parse(PAGE)
    assert all(x.has_360 for x in v if x.model_id == 22040)        # ZEEKR 7X: has360 true on the listing
    assert not any(x.has_360 for x in v if x.model_id == 12847)    # Tesla Model 3: false


VIEW360_PAGE = (Path(__file__).resolve().parent.parent / "fixtures" / "sgcarmart_model_view360_live.html").read_text(encoding="utf-8")
COROLLA = "https://www.sgcarmart.com/new-cars/info/12618/toyota-corolla-altis"
INFO = "https://www.sgcarmart.com/new-cars/info/{}/x"


def _view360_pipeline(cfg, tmp_path, monkeypatch, pages):
    """A pipeline whose model page fetches answer from `pages` ({url: html or exception}); returns it and the urls fetched."""
    from datetime import date

    import pipeline as pl
    from db import Database
    from scrapers.base import BaseScraper

    fetched = []

    def fetch(self, url, *a, **k):
        fetched.append(url)
        page = pages[url]
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(BaseScraper, "fetch", fetch)
    cfg["new_cars"].update(view360_pages_per_run=3, view360_recheck_days=30)
    db = Database(tmp_path / "cars.db")
    return pl.Pipeline(cfg, db, date(2026, 10, 17), "ua"), db, fetched


def test_view360_step_stores_exact_links_caps_pages_and_survives_failures(cfg, tmp_path, monkeypatch):
    from datetime import date

    from scrapers.base import FetchError

    pages = {COROLLA: VIEW360_PAGE, INFO.format(20001): "<html>no viewer here</html>",
             INFO.format(20002): FetchError("timeout"), INFO.format(20003): VIEW360_PAGE.replace("12618", "20003")}
    p, db, fetched = _view360_pipeline(cfg, tmp_path, monkeypatch, pages)
    db.put_view360({20002: INFO.format(20002), 20001: INFO.format(20001), 12618: COROLLA, 20003: INFO.format(20003)},
                   date(2026, 10, 17), exact=False)
    p.run_view360()
    assert fetched == [COROLLA, INFO.format(20001), INFO.format(20002)]     # cap 3, by model id; the failure did not stop the rest
    links = db.view360()
    assert links[12618] == "https://i.i-sgcm.com/new_cars/pano360_viewer.php?specCode=9491&carCode=12618"
    assert links[20001] == INFO.format(20001)
    # the next run opens only the page not checked yet; the page without a viewer and the failed one wait 30 days
    fetched.clear()
    p.run_view360()
    assert fetched == [INFO.format(20003)]
    assert db.view360()[20003].endswith("carCode=20003")
    fetched.clear()
    p.run_view360()
    assert fetched == []
    # 30 days on, the page without a viewer is opened again; exact links never are
    p.run_date = date(2026, 11, 16)
    p.run_view360()
    assert fetched == [INFO.format(20001), INFO.format(20002)]


def _http_error(status):
    import httpx

    from scrapers.base import FetchError

    req = httpx.Request("GET", INFO.format(20001))
    err = FetchError(str(status))
    err.__cause__ = httpx.HTTPStatusError(str(status), request=req, response=httpx.Response(status, request=req))
    return err


@pytest.mark.parametrize("block", ["429", "403", "challenge"])
def test_view360_step_stops_on_a_block_and_leaves_that_page_unchecked(cfg, tmp_path, monkeypatch, block):
    from datetime import date

    page = ("<html><title>Just a moment...</title>Verify you are human</html>" if block == "challenge"
            else _http_error(int(block)))
    pages = {INFO.format(20001): page, INFO.format(20002): VIEW360_PAGE.replace("12618", "20002")}
    p, db, fetched = _view360_pipeline(cfg, tmp_path, monkeypatch, pages)
    db.put_view360({m: INFO.format(m) for m in (20001, 20002)}, date(2026, 10, 17), exact=False)
    p.run_view360()
    assert fetched == [INFO.format(20001)]                  # stopped at the block; the page after it was not opened
    assert [m for m, _ in db.view360_to_check(date(2026, 10, 17), 30, 10)] == [20001, 20002]
