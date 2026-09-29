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
    db.start_run(today)
    assert db.already_sent(today) is False
    db.mark_sent(today)
    db.finish_run(today, "ok")
    assert db.already_sent(today) is True
    db.start_run(today)  # rerun on the same day keeps the sent marker
    assert db.already_sent(today) is True


def test_fuel_price_roundtrip(tmp_path):
    from models import FuelPrice

    db = Database(tmp_path / "t.db")
    db.upsert_fuel_price(FuelPrice(observed_on=date(2026, 9, 29), ron95_per_litre=3.48, by_brand={"SPC": 3.46, "Cnergy": 2.54}, source="t"))
    latest = db.latest_fuel_price()
    assert latest.ron95_per_litre == 3.48 and latest.by_brand == {"SPC": 3.46, "Cnergy": 2.54}
