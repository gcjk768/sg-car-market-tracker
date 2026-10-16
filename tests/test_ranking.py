"""Popularity ranking on the website: each case is a problem the 2026-10-17 audit found in the live data."""
import sqlite3
from datetime import date
from pathlib import Path

import website
from db import Database
from models import Drivetrain, NewCarVariant, NewEvVariant, UsedListing
from scrapers.new_cars import SgcarmartNewCarScraper, parse_best_sellers

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def car(make, model, rank, fuel="petrol", best=None, price=1, variant="", mid=None):
    return NewCarVariant(make=make, model=model, variant=variant, fuel=fuel, price_with_coe=price, popularity=rank,
                         best_list=best[0] if best else None, best_pos=best[1] if best else None, model_id=mid,
                         listing_url="u", source="t")


POP = website.Popularity([
    car("Mercedes-Benz", "CLA Hybrid", 78, "hybrid"), car("Mercedes-Benz", "CLA Electric", 92, "ev"),
    car("Porsche", "Cayenne", 203), car("Porsche", "Cayenne Electric", 243, "ev"),
    car("Audi", "A6 Sedan", 238), car("Audi", "A6 Sportback e-tron Electric", 305, "ev"),
    car("Mercedes-Benz", "EQS SUV Electric", 338, "ev"),
    car("Honda", "N-Box", 194), car("Toyota", "Yaris Cross Hybrid", 79, "hybrid"),
    car("Mazda", "3 Sedan Mild Hybrid", 58, "hybrid"), car("Mazda", "3 Hatchback Mild Hybrid", 191, "hybrid"),
    car("ORA", "Good Cat Electric", 119, "ev"), car("Hyundai", "Kona Hybrid", 77, "hybrid"),
    car("Kia", "Niro Hybrid", 20, "hybrid", best=("Cat A", 1)),
])


def test_an_ev_takes_its_electric_listing_not_the_petrol_one():
    assert POP.of("Mercedes-Benz", "CLA", ev=True) == 92 and POP.of("Mercedes-Benz", "CLA", ev=False) == 78
    assert POP.of("Porsche", "Cayenne", ev=True) == 243
    assert POP.of("Audi", "A6", ev=True) == 305          # the e-tron, through its Sportback body
    assert POP.of("Hyundai", "Kona", ev=True) == 77      # no electric Kona listed: the nameplate's rank


def test_a_short_name_only_widens_across_body_styles():
    assert POP.of("Mercedes-Benz", "EQS", ev=True) is None   # the EQS SUV is another car
    assert POP.of("Honda", "N") is None                      # N-VAN is not the N-Box
    assert POP.of("Toyota", "YARIS") is None                 # the Yaris is not the Yaris Cross
    assert POP.of("Mazda", "3", ev=False) == 58              # the best of Sedan and Hatchback


def test_used_site_spellings_reach_the_model():
    def used(make, model, variant, dt=Drivetrain.ice):
        return UsedListing(source="m", listing_id="1", url="u", make=make, model=model, variant=variant, drivetrain=dt, price=1)
    assert website._used_rank(POP, used("Mazda", "3 4DR", "1.5 AT M-HYBRID CLASSIC I2", Drivetrain.hybrid)) == 58
    assert website._used_rank(POP, used("GREAT", "WALL", "ORA GOOD CAT ELECTRIC SUNROOF", Drivetrain.ev)) == 119
    assert website._used_rank(POP, used("Honda", "N", "VAN STYLE FUN TURBO 660 CVT")) is None


def test_best_sellers_come_from_the_published_lists():
    assert POP.best("Kia", "Niro", ev=False) == {"list": "Cat A", "pos": 1}
    best = parse_best_sellers((FIX / "sgcarmart_best_sellers.html").read_text(encoding="utf-8"))
    by_list = {}
    for name, pos in best.values():
        by_list.setdefault(name, []).append(pos)
    assert {k: sorted(v) for k, v in by_list.items()} == {"Cat A": list(range(1, 9)), "Cat B": list(range(1, 9)),
                                                         "Parallel import": list(range(1, 9))}


def test_a_failed_page_keeps_its_places(cfg):
    def m(i):
        return {"id": i, "make": "X", "model": f"M{i}", "slug": "x", "subModels": [{"name": "b", "price": 100000}]}
    v = SgcarmartNewCarScraper(cfg, "ua").variants([m(1), m(2), {"gap": 15}, m(3)], best={2: ("Cat B", 4)})
    got = {x.model: (x.popularity, x.best_list, x.best_pos) for x in v}
    assert got == {"M1": (1, None, None), "M2": (2, "Cat B", 4), "M3": (18, None, None)}


def test_an_ev_on_both_lists_shows_once(cfg):
    ev_list = [NewEvVariant(make="Audi", model="Q4", variant="40", price_with_coe=347999, listing_url="u", price_source_url="u", source="t")]
    same = car("Audi", "Q4 Sportback e-tron Electric", 205, "ev", price=347999)
    other = car("Hyundai", "Ioniq 5 N Electric", 300, "ev", price=376888)
    assert website.same_car(same, ev_list) and not website.same_car(other, ev_list)


def test_two_models_with_the_same_name_both_keep_their_rows(tmp_path):
    db = Database(tmp_path / "c.db")
    d = date(2026, 10, 17)
    db.upsert_new_cars([car("Toyota", "Noah Hybrid", 36, variant="1.8 Elegance", mid=22038, price=224888),
                        car("Toyota", "Noah Hybrid", 198, variant="1.8 Elegance", mid=22079, price=232400)], d)
    assert sorted(v.popularity for v in db.new_cars_on(d)) == [36, 198]
    db.upsert_new_cars([car("Toyota", "Noah Hybrid", 40, variant="1.8 Elegance", mid=22038)], d)   # a re-run replaces the day
    assert [v.popularity for v in db.new_cars_on(d)] == [40]


def test_the_first_schema_is_migrated_without_losing_rows(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE new_car_prices (scraped_on TEXT NOT NULL, source TEXT NOT NULL, make TEXT NOT NULL,
        model TEXT NOT NULL, variant TEXT NOT NULL DEFAULT '', fuel TEXT NOT NULL, price_with_coe INTEGER NOT NULL,
        price_includes_coe INTEGER NOT NULL DEFAULT 1, power_kw REAL, engine_cc INTEGER, fuel_economy TEXT, transmission TEXT,
        price_change INTEGER, price_change_on TEXT, launch_date TEXT, model_id INTEGER, listing_url TEXT NOT NULL,
        scraped_at TEXT NOT NULL, PRIMARY KEY (scraped_on, source, make, model, variant))""")
    con.execute("INSERT INTO new_car_prices VALUES ('2026-10-17','t','Toyota','Raize','1.0',"
                "'petrol',150000,1,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'u','2026-10-17T01:00:00')")
    con.commit(); con.close()
    db = Database(path)
    pk = [r["name"] for r in db.conn.execute("PRAGMA table_info(new_car_prices)") if r["pk"]]
    assert "model_id" in pk and [v.model for v in db.new_cars_on(date(2026, 10, 17))] == ["Raize"]


def test_a_ranked_sibling_never_wears_the_best_seller_badge(cfg):
    pop = website.Popularity([car("Mercedes-Benz", "GLB Mild Hybrid", 29, "hybrid", best=("Cat A", 2)),
                              car("Mercedes-Benz", "GLB Hybrid", 550, "hybrid"),
                              car("Honda", "WR-V", 113, best=("Parallel import", 5))])
    cards = website.new_car_cards([car("Mercedes-Benz", "GLB Hybrid", 550, "hybrid"), car("Honda", "WR-V Vin's Auto Edition", None)],
                                  [], cfg, date(2026, 10, 17), 3.5, {"ev": None, "ice": None}, {}, pop)
    glb, wrv = cards
    assert glb["best_seller"] is None and glb["popularity"] == 550
    assert wrv["popularity"] == 113 and wrv["best_seller"] == {"list": "Parallel import", "pos": 5}   # spotlight-only edition
