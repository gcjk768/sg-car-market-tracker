from datetime import date

from db import Database
from models import CoeResult, UsedListing


def _listing(listing_id="1", price=100000, **kw):
    base = dict(source="test", listing_id=listing_id, url=f"https://example.com/{listing_id}", make="BYD", model="Atto 3", drivetrain="ev", price=price, year=2023, mileage_km=20000, owners=1)
    base.update(kw)
    return UsedListing(**base)


def test_coe_upsert_is_idempotent(tmp_path):
    db = Database(tmp_path / "t.db")
    r = CoeResult(tender_date=date(2026, 9, 23), exercise="2026-09 second", category="A", quota_premium=104000, source="test")
    db.upsert_coe_results([r, r])
    db.upsert_coe_results([r.model_copy(update={"quota_premium": 105000})])
    hist = db.coe_history("A")
    assert len(hist) == 1
    assert hist[0].quota_premium == 105000
    assert [x.category.value for x in db.latest_coe()] == ["A"]


def test_used_listing_new_then_drop_then_gone(tmp_path):
    db = Database(tmp_path / "t.db")
    d1, d2, d3 = date(2026, 9, 27), date(2026, 9, 28), date(2026, 9, 29)
    stats = db.upsert_used_listings([_listing(price=100000)], d1)
    assert stats == {"new": 1, "updated": 0, "drops": 0}
    stats = db.upsert_used_listings([_listing(price=100000)], d1)
    assert stats["new"] == 0 and stats["drops"] == 0
    stats = db.upsert_used_listings([_listing(price=98000)], d2)
    assert stats["drops"] == 1
    active = db.active_listings(["ev"])
    assert len(active) == 1
    assert active[0].first_seen == d1 and active[0].last_seen == d2
    assert [p.price for p in active[0].price_history] == [100000, 98000]
    assert db.mark_gone("test", d3) == 1
    assert db.active_listings() == []
    assert db.count_gone_since(d2) == 1


def test_run_bookkeeping(tmp_path):
    db = Database(tmp_path / "t.db")
    today = date(2026, 9, 29)
    status = lambda: db.conn.execute("SELECT status FROM runs WHERE run_date = ?", (today.isoformat(),)).fetchone()["status"]
    db.start_run(today)
    assert status() == "started"
    db.finish_run(today, "ok")
    assert status() == "ok"
    db.start_run(today)  # a rerun on the same day reuses the row
    assert status() == "started"


def test_fuel_price_roundtrip(tmp_path):
    from models import FuelPrice

    db = Database(tmp_path / "t.db")
    db.upsert_fuel_price(FuelPrice(observed_on=date(2026, 9, 29), ron95_per_litre=3.48, by_brand={"SPC": 3.46, "Cnergy": 2.54}, source="t"))
    latest = db.latest_fuel_price()
    assert latest.ron95_per_litre == 3.48 and latest.by_brand == {"SPC": 3.46, "Cnergy": 2.54}


def test_view360_keeps_the_exact_viewer_and_drops_models_that_lost_it(tmp_path):
    db = Database(tmp_path / "t.db")
    viewer = "https://i.i-sgcm.com/new_cars/pano360_viewer.php?specCode=10194&carCode=22040"
    db.put_view360({22040: viewer}, date(2026, 10, 17), exact=True)
    db.put_view360({22040: "https://www.sgcarmart.com/new-cars/info/22040/zeekr-7x-electric",
                    21506: "https://www.sgcarmart.com/new-cars/info/21506/tesla-model-y-electric"}, date(2026, 10, 17), exact=False)
    assert db.view360() == {22040: viewer, 21506: "https://www.sgcarmart.com/new-cars/info/21506/tesla-model-y-electric"}
    db.put_view360({}, date(2026, 10, 18), exact=False, gone={21506})
    assert db.view360() == {22040: viewer}
