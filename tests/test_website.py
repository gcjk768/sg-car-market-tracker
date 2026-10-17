"""The website data file: built from the database, every car with its costings, written atomically."""
import json
from datetime import date, datetime

import pytest

import website as site
from db import Database
from models import CoeCategory, CoeResult, Drivetrain, FuelPrice, NewCarVariant, NewEvVariant, UsedListing

TODAY = date(2026, 10, 16)


def _db(tmp_path):
    db = Database(tmp_path / "cars.db")
    for cat, prem in (("A", 130001), ("B", 130100)):
        for n in range(5):
            db.upsert_coe_results([CoeResult(tender_date=date(2026, 8, 5) + (date(2026, 8, 19) - date(2026, 8, 5)) * n, exercise="x",
                                             category=CoeCategory(cat), quota_premium=prem - 1000 * (4 - n), quota=1000,
                                             bids_received=1300, bids_successful=990, source="t")])
    db.upsert_new_ev([
        NewEvVariant(make="BYD", model="Atto 3", variant="Evo 60 kWh", price_with_coe=184888, coe_category=CoeCategory.A,
                     battery_kwh=60, range_km=381, power_kw=150, listing_url="https://x/atto3", price_source_url="https://x", source="t"),
        NewEvVariant(make="Tesla", model="Model 3", variant="RWD", price_with_coe=186999, coe_category=CoeCategory.B,
                     battery_kwh=62.5, range_km=481, power_kw=208, listing_url="https://x/m3", price_source_url="https://x", source="t"),
        NewEvVariant(make="Citroen", model="e-Dispatch", variant="75 kWh", price_with_coe=132000, power_kw=100,
                     listing_url="https://x/van", price_source_url="https://x", source="t"),   # a van: not on the site
        NewEvVariant(make="Zeekr", model="7X", variant="RWD", price_with_coe=269999, coe_category=CoeCategory.B,
                     listing_url="https://x/7x", price_source_url="https://x", source="t"),   # no power: no road tax, still listed
    ], TODAY)
    good = UsedListing(source="sgcarmart", listing_id="1", url="https://x/1", make="BYD", model="Dolphin", variant="Dynamic",
                       drivetrain=Drivetrain.ev, year=2023, reg_date=date(2023, 8, 1), mileage_km=26000, owners=1, price=118800,
                       coe_expiry=date(2033, 7, 31), coe_years_remaining=6.8, omv=30000, arf=0, power_kw=70, first_seen=TODAY, last_seen=TODAY)
    van = UsedListing(source="motorist", listing_id="2", url="https://x/2", make="Toyota", model="DYNA 150", variant="",
                      drivetrain=Drivetrain.ice, year=2023, reg_date=date(2023, 8, 7), mileage_km=None, owners=1, price=95988,
                      coe_years_remaining=6.8, engine_cc=2755, flags=["commercial vehicle"], first_seen=date(2026, 9, 30), last_seen=TODAY)
    db.upsert_used_listings([good, van], date(2026, 10, 15))
    good.price = 115800   # a drop since yesterday
    db.upsert_used_listings([good, van], TODAY)
    db.upsert_new_cars([
        NewCarVariant(make="Toyota", model="Corolla Altis", variant="1.6 Elegance", fuel="petrol", price_with_coe=168888, power_kw=90,
                      engine_cc=1600, popularity=40, listing_url="https://x/altis", source="t"),
        NewCarVariant(make="BMW", model="X5", variant="xDrive40i 3.0", fuel="petrol", price_with_coe=420000, power_kw=250,
                      engine_cc=3000, listing_url="https://x/x5", source="t"),
        NewCarVariant(make="BYD", model="Atto 3 Electric", variant="Evo", fuel="ev", price_with_coe=184888, power_kw=150,
                      popularity=10, listing_url="https://x/a3", source="t"),
        NewCarVariant(make="BYD", model="Dolphin Electric", variant="Dynamic", fuel="ev", price_with_coe=170000, power_kw=70,
                      popularity=25, listing_url="https://x/dolphin", source="t"),     # already on the EV price list: left out
    ], TODAY)
    db.upsert_fuel_price(FuelPrice(observed_on=TODAY, ron95_per_litre=3.57, by_brand={"SPC": 3.53, "Shell": 3.57},
                                   grades={"SPC": {"95": 3.53}}, source="https://x"))
    return db


@pytest.fixture
def offline(monkeypatch):
    """No LTA tables and no SGBikemart: the site still builds, without brand ranks or bikes."""
    monkeypatch.setattr(site, "scrape_registrations", lambda *a, **k: None)
    monkeypatch.setattr(site, "scrape_body_types", lambda *a, **k: None)
    monkeypatch.setattr(site, "scrape_motorbikes", lambda *a, **k: {"used": {}, "brands": None})


def test_body_type_uses_the_longest_matching_key_and_ignores_hyphens(cfg):
    assert site.body_type("Mazda", "3 HB", "", cfg) == "Hatchback"
    assert site.body_type("Mazda", "3 4DR", "", cfg) == "Sedan"
    assert site.body_type("Mercedes BENZ", "C", "C180", cfg) == "Sedan"
    assert site.body_type("Citroen", "e-C4 X Sedan", "", cfg) == "Sedan"
    assert site.body_type("Smart", "#3", "", cfg) == "SUV"
    assert site.body_type("Unknown", "Thing", "5 door hatchback", cfg) == "Hatchback"
    assert site.body_type("Unknown", "Thing", "", cfg) == "Other"


def test_build_has_every_car_with_costs_and_flags(cfg, tmp_path, offline):
    db = _db(tmp_path)
    data = site.build(cfg, db, TODAY, "ua")
    json.dumps(data)   # plain types only
    assert data["stats"] == {"new_ev_variants": 3, "new_ev_brands": 3, "official_matched": 0, "official_brands": 0, "coe_added": 0, "new_car_variants": 3, "new_car_models": 3, "used_active": 2, "used_passing": 1,
                             "new_today": 0, "drops": 1, "gone": 0}   # first seen yesterday
    atto = next(c for c in data["new_evs"] if c["model"] == "Atto 3")
    assert atto["body"] == "SUV" and atto["cat"] == "A" and atto["finance"]["deposit"] == 73955
    assert atto["annual"]["total_low"] > 20000 and atto["price_next"] is not None
    zeekr = next(c for c in data["new_evs"] if c["make"] == "Zeekr")
    assert zeekr["body"] == "SUV" and zeekr["price_next"] is not None   # no power rating, still listed and costed
    dolphin = next(c for c in data["used"] if c["model"] == "Dolphin")
    assert dolphin["passes"] and dolphin["prev_price"] == 118800 and dolphin["body"] == "Hatchback" and dolphin["days_listed"] == 1
    assert dolphin["finance"]["deposit"] == 46320 and dolphin["history"][-1]["price"] == 115800
    van = next(c for c in data["used"] if c["source"] == "motorist")
    assert not van["passes"] and van["reject_reason"] == "commercial vehicle" and van["body"] == "Other"
    assert [r["cat"] for r in data["coe"]["rows"]] == ["A", "B"]
    assert data["coe"]["rows"][0]["delta"] == 1000 and len(data["coe"]["rows"][0]["history"]) == 5
    assert data["coe"]["rows"][0]["forecast"]["low"] < data["coe"]["rows"][0]["forecast"]["point"]
    assert data["coe"]["next_results"] > TODAY.isoformat() and data["coe"]["bidding_opens"] < data["coe"]["bidding_closes"]
    assert data["fuel"]["ron95"] == 3.57
    by_model = {c["model"]: c for c in data["new_cars"]}
    x5, altis = by_model["X5"], by_model["Corolla Altis"]
    assert by_model["Dolphin Electric"]["fuel"] == "ev"   # not on the EV price list, so the full list supplies it
    assert altis["cat"] == "A" and altis["fuel"] == "petrol" and altis["body"] == "Sedan" and altis["annual"]["energy"] > 3000
    move = {r["cat"]: r["forecast"]["point"] - r["premium"] for r in data["coe"]["rows"]}
    # Sgcarmart's popularity rank reaches every card: the full list's own, the EV list's by model name, used cars by model
    assert altis["popularity"] == 40 and x5["popularity"] is None
    assert atto["popularity"] == 10 and zeekr["popularity"] is None
    assert dolphin["popularity"] == 25 and van["popularity"] is None
    assert x5["cat"] == "B" and x5["price_next"][1] == 420000 + move["B"] and altis["price_next"][1] == 168888 + move["A"]
    assert [p["label"] for p in data["costs"]["picks"]] == ["New EV", "Used EV"]   # the van never reaches the ICE pick
    assert data["brands"] == {} and data["bikes"]["classes"] == {}
    assert data["guide"]["rule_of_thumb"].startswith("When Cat A is above $90,000")


def test_write_is_atomic_and_publish_never_raises(cfg, tmp_path, offline, monkeypatch):
    out = tmp_path / "site" / "data.json"
    site.write({"a": 1, "when": datetime(2026, 10, 16)}, out)
    assert json.loads(out.read_text(encoding="utf-8"))["a"] == 1 and not out.with_suffix(".tmp").exists()
    cfg["site"]["output"] = str(out)
    assert site.publish(cfg, _db(tmp_path), TODAY, "ua") == out
    monkeypatch.setattr(site, "build", lambda *a, **k: 1 / 0)
    assert site.publish(cfg, _db(tmp_path), TODAY, "ua") is None   # logged, the report goes on


def test_popularity_lookup_matches_spellings_and_whole_words():
    pop = site.Popularity([
        NewCarVariant(make="BYD", model="Seal Electric", fuel="ev", price_with_coe=1, popularity=30, listing_url="u", source="t"),
        NewCarVariant(make="BYD", model="Seal 6 Electric", fuel="ev", price_with_coe=1, popularity=17, listing_url="u", source="t"),
        NewCarVariant(make="Mercedes-Benz", model="GLB Mild Hybrid", fuel="hybrid", price_with_coe=1, popularity=30, listing_url="u", source="t"),
        NewCarVariant(make="Mercedes-Benz", model="GLB Electric", fuel="ev", price_with_coe=1, popularity=39, listing_url="u", source="t"),
    ])
    assert pop.of("BYD", "Seal 6") == 17 and pop.of("BYD", "SEAL") == 30 and pop.of("byd", "Seal 6 Premium") == 17
    assert pop.of("BYD", "Sealion 7") is None          # "Seal" is not a word prefix of "Sealion"
    # fuel words go, so GLB Electric and GLB Mild Hybrid are one family and share its best rank
    assert pop.of("Mercedes BENZ", "GLB") == 30 and pop.of("Mercedes-Benz", "GLB Mild Hybrid 200") == 30
    assert pop.of("MG", "S6 EV") is None and pop.of("BYD", "Seal 6 Electric") == 17
    assert pop.of("Mercedes-Benz", "") is None          # a bare make never takes a rank


def test_an_ev_on_both_lists_keeps_its_official_price_and_shows_once(cfg, tmp_path, offline):
    """The listing copy of an EV must not take the official row and then be dropped as a duplicate."""
    db = _db(tmp_path)
    db.upsert_new_cars([NewCarVariant(make="BYD", model="Atto 3 Electric", variant="Evo 60 kWh", fuel="ev", price_with_coe=184888,
                                      power_kw=150, listing_url="https://x/a3l", source="t")], TODAY)
    db.upsert_official([{"make": "BYD", "model": "Atto 3", "variant": "Evo", "price": 182888, "includes_coe": "with"}], TODAY, {"BYD"})
    data = site.build(cfg, db, TODAY, "ua")
    attos = [c for c in data["new_evs"] + data["new_cars"] if c["make"] == "BYD" and "Atto 3" in c["model"]]
    assert len(attos) == 1 and attos[0]["price"] == 182888 and attos[0]["price_source"] == "official"


def test_car_image_comes_from_the_model_id_in_the_price_list_link(cfg):
    url = "https://www.sgcarmart.com/new-cars/info/21974/aion-hyptec-ht-electric"
    assert site.car_image(url, cfg) == "https://i.i-sgcm.com/new_cars/cars/21974/21974_m.jpg"
    assert site.car_image("https://www.sgcarmart.com/new-cars/pricelist", cfg) is None
    assert site.car_image(None, cfg) is None
