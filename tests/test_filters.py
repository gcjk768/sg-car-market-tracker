from datetime import date

from filters import reject_reason, shortlist, tag_for
from models import PricePoint, UsedListing

TODAY = date(2026, 9, 29)


def make(**kw) -> UsedListing:
    base = dict(
        source="t", listing_id="1", url="https://x/1", make="BYD", model="Atto 3", drivetrain="ev",
        reg_date=date(2023, 3, 12), mileage_km=28000, owners=1, price=118800,
        depreciation_per_year=11900, coe_years_remaining=7.4, description="",
    )
    base.update(kw)
    return UsedListing(**base)


def test_passes_all_filters(cfg):
    assert reject_reason(make(), cfg, TODAY) is None


def test_each_filter(cfg):
    assert reject_reason(make(price=120001), cfg, TODAY) == "price above ceiling"
    assert reject_reason(make(mileage_km=None), cfg, TODAY) == "missing mileage"
    assert reject_reason(make(reg_date=None, year=None), cfg, TODAY) == "missing age"
    assert reject_reason(make(reg_date=date(2020, 1, 1)), cfg, TODAY) == "too old"
    assert reject_reason(make(drivetrain="ice", reg_date=date(2020, 1, 1), engine_cc=1500), cfg, TODAY) is None
    assert reject_reason(make(mileage_km=80000), cfg, TODAY) == "mileage too high for age"
    assert reject_reason(make(owners=3), cfg, TODAY) == "too many owners"
    assert reject_reason(make(coe_years_remaining=None), cfg, TODAY) == "missing COE"
    assert reject_reason(make(coe_years_remaining=5.9), cfg, TODAY) == "COE too short"
    assert reject_reason(make(description="Sold as is"), cfg, TODAY) == "excluded keyword: as is"
    assert reject_reason(make(flags=["accident"]), cfg, TODAY) == "excluded keyword: accident"
    assert reject_reason(make(description="For export only"), cfg, TODAY) == "excluded keyword: export"


def test_new_car_mileage_uses_half_year_floor(cfg):
    # Registered two months ago with 6,000 km is fine, it is under 15,000 km for half a year.
    assert reject_reason(make(reg_date=date(2026, 7, 29), mileage_km=6000), cfg, TODAY) is None
    assert reject_reason(make(reg_date=date(2026, 7, 29), mileage_km=9000), cfg, TODAY) == "mileage too high for age"


def test_ranking_and_bonus(cfg):
    a = make(listing_id="a", depreciation_per_year=12000, mileage_km=30000)
    b = make(listing_id="b", depreciation_per_year=12000, mileage_km=20000)
    c = make(listing_id="c", depreciation_per_year=12500, mileage_km=10000, description="Full service history")
    d = make(listing_id="d", depreciation_per_year=12500, mileage_km=10000)
    top, rejected = shortlist([a, b, c, d, make(listing_id="x", price=200000)], cfg, TODAY, top_n=3)
    # c gets a 5 percent bonus so its adjusted depreciation is 11,875, ahead of a and b.
    assert [l.listing_id for l in top] == ["c", "b", "a"]
    assert rejected == {"price above ceiling": 1}


def test_tags():
    today = date(2026, 9, 29)
    new = make(first_seen=today, price_history=[PricePoint(seen_on=today, price=118800)])
    assert tag_for(new, today) == "NEW"
    dropped = make(first_seen=date(2026, 9, 20), price=116800, price_history=[
        PricePoint(seen_on=date(2026, 9, 20), price=118800), PricePoint(seen_on=today, price=116800)])
    assert tag_for(dropped, today) == "DROP ▼2,000"
    steady = make(first_seen=date(2026, 9, 20), price_history=[PricePoint(seen_on=date(2026, 9, 20), price=118800)])
    assert tag_for(steady, today) == ""
    # A since date earlier than first_seen marks it NEW even if not seen today.
    assert tag_for(steady, date(2026, 9, 15)) == "NEW"


def test_negated_accident_wording_is_not_excluded(cfg):
    from filters import strip_negated

    assert reject_reason(make(description="Accident free, agent maintained"), cfg, TODAY) is None
    assert reject_reason(make(description="No accident history, one owner"), cfg, TODAY) is None
    assert reject_reason(make(description="Never been in an accident"), cfg, TODAY) is None
    assert reject_reason(make(description="Minor accident repaired"), cfg, TODAY) == "excluded keyword: accident"
    assert "accident" not in strip_negated("non-accident car with accident-free record")
