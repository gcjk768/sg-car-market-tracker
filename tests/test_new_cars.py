"""Sgcarmart's full new car listing: the React payload parsed from a trimmed live page (2026-10-17)."""
from pathlib import Path

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
