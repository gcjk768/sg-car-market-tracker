from datetime import date
from pathlib import Path

from scrapers.motorbikes import parse_mc_registrations, parse_used_bikes, shortlist
from settings import load_config

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def test_used_bike_cards_parse_from_the_live_page():
    bikes = parse_used_bikes((FIX / "sgbikemart_used_live.html").read_text(encoding="utf-8"), "2")
    assert len(bikes) == 10
    mt = next(b for b in bikes if b.title == "Yamaha MT-07 Tracer")
    assert (mt.price, mt.reg_date, mt.cc, mt.mileage_km) == (19800, date(2022, 5, 26), 689, 18000)
    assert mt.url.startswith("https://sgbikemart.com.sg/listing/usedbike/")
    assert mt.coe_years_left(date(2026, 10, 2)) == 5.6 and mt.depreciation(date(2026, 10, 2)) == 3535


def test_shortlist_drops_short_coe_and_ranks_by_depreciation():
    cfg = load_config()
    bikes = parse_used_bikes((FIX / "sgbikemart_used_live.html").read_text(encoding="utf-8"), "2")
    picked = shortlist(bikes, cfg, date(2026, 10, 2))
    assert all(b.coe_years_left(date(2026, 10, 2)) >= cfg["motorbikes"]["filters"]["min_coe_years_remaining"] for b in picked)
    deps = [b.depreciation(date(2026, 10, 2)) for b in picked]
    assert deps == sorted(deps) and "Ducati Monster 1100 EVO" not in [b.title for b in picked]  # 2010, COE expired


def test_lta_m04_brands():
    makes, months = parse_mc_registrations((FIX / "lta_m04_text.txt").read_text(encoding="utf-8"))
    assert makes["YAMAHA"]["total"] == 4271 and makes["BMW"] == {"total": 137, "ev": 1, "petrol": 136}
    assert makes["GOGORO"]["ev"] == 8 and months[0] == "2026-01" and months[-1] == "2026-08"
