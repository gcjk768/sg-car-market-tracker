from datetime import date
from pathlib import Path

from models import CoeCategory, NewEvVariant
from scrapers.fuel_price import FuelPriceScraper, parse_ron95_prices
from scrapers.new_ev import BrandPriceChecker, SgcarmartNewEvScraper, rank_new_evs, warranty_years

FIX = Path(__file__).resolve().parent.parent / "fixtures"
RUN = date(2026, 9, 29)


def test_index_keeps_only_electric_models(cfg):
    s = SgcarmartNewEvScraper(cfg, "ua", RUN)
    models = s.parse((FIX / "sgcarmart_new_index.html").read_text())
    assert [m["slug"] for m in models] == ["13501", "13502", "mg-mgs5-ev"]
    assert models[0]["url"] == "https://www.sgcarmart.com/new_cars/newcars_overview.php?CarCode=13501"


def test_model_page_variants(cfg):
    s = SgcarmartNewEvScraper(cfg, "ua", RUN)
    variants = s.parse_model((FIX / "sgcarmart_new_model.html").read_text(), "https://x/model3")
    assert [v.variant for v in variants] == ["RWD 110", "Premium RWD 110", "Long Range AWD"]
    v = variants[0]
    assert v.make == "Tesla" and v.model == "Model 3"
    assert v.price_with_coe == 179999 and v.price_without_coe == 48109
    assert v.power_kw == 110 and v.coe_category == CoeCategory.A
    assert variants[2].coe_category == CoeCategory.B
    assert v.range_km == 534 and v.range_standard == "WLTP"
    assert v.battery_kwh == 62.5
    assert v.battery_warranty_years == 8 and "160,000" in v.battery_warranty
    assert v.ves_band == "A" and v.ves_rebate == 22500
    assert "wall connector" in v.promotion
    assert round(v.score) == 337


def test_warranty_years():
    assert warranty_years("8 years / 160,000 km") == 8
    assert warranty_years(None) is None


def test_brand_check_confirms_prices(cfg):
    s = SgcarmartNewEvScraper(cfg, "ua", RUN)
    variants = s.parse_model((FIX / "sgcarmart_new_model.html").read_text(), "https://x/model3")
    checker = BrandPriceChecker(cfg, "ua", RUN)
    checker.fetch = lambda url, params=None: (FIX / "tesla_brand_page.html").read_text()
    notes = checker.check(variants)
    assert notes["Tesla"] == "2 of 3 prices confirmed on brand page"
    assert variants[0].price_source_url == cfg["sources"]["brand_pages"]["Tesla"]
    assert variants[2].price_source_url == "https://x/model3"


def _v(make, model, price, rng, wty=8):
    return NewEvVariant(make=make, model=model, price_with_coe=price, range_km=rng, battery_warranty_years=wty,
                        listing_url="u", price_source_url="u", source="t")


def test_rank_new_evs_top_n_plus_always_include(cfg):
    cfg["new_ev"]["top_n"] = 2
    variants = [
        _v("Tesla", "Model 3", 179999, 534),
        _v("GAC Aion", "UT", 148988, 410),
        _v("BYD", "Dolphin", 165888, 345),
        _v("Xpeng", "G6", 213899, 435),
        _v("BYD", "Atto 3", 171888, 420),
    ]
    ranked = rank_new_evs(variants, cfg)
    assert [f"{v.make} {v.model}" for v in ranked] == ["Tesla Model 3", "GAC Aion UT", "BYD Atto 3", "Xpeng G6"]


def test_tiebreak_longer_battery_warranty(cfg):
    a = _v("A", "One", 100000, 400, wty=6)
    b = _v("B", "Two", 100000, 400, wty=8)
    assert rank_new_evs([a, b], cfg)[0].make == "B"


def test_fuel_price_table(cfg):
    html = (FIX / "motorist_fuel.html").read_text()
    prices = parse_ron95_prices(html)
    assert prices["SPC"] == 3.46 and prices["Shell"] == 3.49 and prices["Cnergy"] == 2.54
    s = FuelPriceScraper(cfg, "ua", RUN)
    s._source = "fixture"
    fp = s.parse(html)[0]
    # Median of 2.54, 3.46, 3.47, 3.48, 3.48, 3.49 is 3.475, so Cnergy does not drag the estimate down.
    assert fp.ron95_per_litre == 3.48
    assert fp.by_brand["Cnergy"] == 2.54 and len(fp.by_brand) == 6
    cfg["costs"]["energy"]["ice"]["price_pick"] = "min"
    assert s.parse(html)[0].ron95_per_litre == 2.54
    cfg["costs"]["energy"]["ice"]["price_pick"] = "avg"
    assert s.parse(html)[0].ron95_per_litre == 3.32
