from datetime import date

import pytest

from costs import (
    arf_from_omv,
    depreciation_new,
    depreciation_used,
    energy_cost,
    fixed_extras,
    for_new_ev,
    for_used,
    insurance_range,
    parf_rebate,
    road_tax,
    road_tax_ev,
    road_tax_hybrid,
    road_tax_petrol,
)
from models import Drivetrain, NewEvVariant, UsedListing

TODAY = date(2026, 9, 29)


def test_road_tax_ev_known_values(cfg):
    # 110 kW: (475 + 7.5 x 20) x 0.782 = 488.75 per 6 months, x2 = 977.5, plus 700.
    assert road_tax_ev(110, cfg) == 1678
    # 7.5 kW floor band: 200 x 0.782 x 2 + 700.
    assert road_tax_ev(7.5, cfg) == 1013
    # 150 kW: (475 + 7.5 x 60) x 0.782 x 2 + 700 = 2147.
    assert road_tax_ev(150, cfg) == 2147
    # 250 kW: (1525 + 10 x 20) x 0.782 x 2 + 700.
    assert road_tax_ev(250, cfg) == 3398


def test_road_tax_petrol_known_values(cfg):
    # 1598 cc: (500 + 0.75 x 598) x 0.782 = 741.73 per 6 months.
    assert road_tax_petrol(1598, cfg) == 1483
    assert road_tax_petrol(600, cfg) == 626
    assert road_tax_petrol(998, cfg) == 781
    assert road_tax_petrol(3500, cfg) == 6334


def test_road_tax_hybrid_takes_higher(cfg):
    # 1598 cc petrol side is 1483, a 53 kW motor is only 526, so engine wins.
    assert road_tax_hybrid(1598, 53, cfg) == 1483
    # A 1.5 litre with a 150 kW motor pays the power based figure (1447) instead.
    assert road_tax_hybrid(1498, 150, cfg) == 1447


def test_road_tax_dispatch(cfg):
    assert road_tax(Drivetrain.ev, None, 110, cfg) == 1678
    assert road_tax(Drivetrain.ice, 1598, None, cfg) == 1483
    assert road_tax(Drivetrain.ev, None, None, cfg) is None


def test_arf_from_omv(cfg):
    assert arf_from_omv(20000, cfg) == 20000
    assert arf_from_omv(40000, cfg) == 48000
    assert arf_from_omv(31842, cfg) == 36579
    assert arf_from_omv(100000, cfg) == 20000 + 28000 + 38000 + 50000 + 64000


def test_parf_schedules(cfg):
    old = date(2022, 6, 1)
    mid = date(2024, 6, 1)
    new = date(2026, 6, 1)
    assert parf_rebate(40000, 4.9, old, cfg) == 30000     # 75 percent, no cap
    assert parf_rebate(100000, 4.9, old, cfg) == 75000    # no cap before Feb 2023
    assert parf_rebate(100000, 4.9, mid, cfg) == 60000    # capped
    assert parf_rebate(40000, 10, mid, cfg) == 20000      # 50 percent at exactly 10 years
    assert parf_rebate(40000, 10.1, mid, cfg) == 0        # nothing after 10 years
    assert parf_rebate(40000, 4.9, new, cfg) == 12000     # 30 percent
    assert parf_rebate(40000, 10, new, cfg) == 2000       # 5 percent
    assert parf_rebate(200000, 4.9, new, cfg) == 30000    # new cap


def test_depreciation_used_with_and_without_arf(cfg):
    base = dict(source="t", listing_id="1", url="u", make="BYD", model="Atto 3", drivetrain="ev",
                price=118800, reg_date=date(2023, 3, 12), coe_years_remaining=6.4)
    # Age at end of COE is 3.55 + 6.4 = 9.95, so 50 percent of ARF 28,079 = 14,040 comes back.
    with_arf = UsedListing(**base, arf=28079)
    assert depreciation_used(with_arf, cfg, TODAY) == round((118800 - 14040) / 6.4)
    # A COE that runs past the tenth birthday means no PARF at all.
    renewed = UsedListing(**{**base, "coe_years_remaining": 6.6}, arf=28079)
    assert depreciation_used(renewed, cfg, TODAY) == round(118800 / 6.6)
    # ARF unknown but OMV known: ARF estimated from OMV first (36,579, half of it is 18,290).
    from_omv = UsedListing(**base, omv=31842)
    assert depreciation_used(from_omv, cfg, TODAY) == round((118800 - 18290) / 6.4)
    # Site quoted dereg value wins over any estimate.
    quoted = UsedListing(**base, arf=28079, dereg_value=32180)
    assert depreciation_used(quoted, cfg, TODAY) == round((118800 - 32180) / 6.4)
    # Nothing known: whole price is written off.
    bare = UsedListing(**base)
    assert depreciation_used(bare, cfg, TODAY) == round(118800 / 6.4)
    assert depreciation_used(UsedListing(**{**base, "coe_years_remaining": None}), cfg, TODAY) is None


def test_depreciation_new(cfg):
    # Registered today under the 2026 schedule: 5 percent of ARF at year 10.
    assert depreciation_new(179999, 20000, cfg, TODAY) == round((179999 - 1000) / 10)
    assert depreciation_new(179999, None, cfg, TODAY) == 18000


def test_energy_cost(cfg):
    # 15,000 km x 16 kWh/100 km = 2,400 kWh at 0.7 x 0.30 + 0.3 x 0.60 = 0.39.
    assert energy_cost(Drivetrain.ev, cfg) == 936
    # 15,000 km x 8 L/100 km = 1,200 L at the fallback 3.48.
    assert energy_cost(Drivetrain.ice, cfg) == 4176
    assert energy_cost(Drivetrain.ice, cfg, petrol_price=3.00) == 3600
    assert energy_cost(Drivetrain.hybrid, cfg, petrol_price=3.00) == 2475


def test_insurance_and_extras(cfg):
    assert insurance_range(118800, Drivetrain.ev, cfg) == (1800, 2800)
    assert insurance_range(79999, Drivetrain.ice, cfg) == (1000, 1600)
    assert insurance_range(500000, Drivetrain.hybrid, cfg) == (2500, 4000)
    assert fixed_extras(Drivetrain.ev, cfg) == 800 + 300 + 1200 + 600
    assert fixed_extras(Drivetrain.ice, cfg) == 1200 + 300 + 1200 + 600


def test_breakdowns(cfg):
    used = UsedListing(source="t", listing_id="1", url="u", make="Toyota", model="Corolla Altis", variant="1.6 Hybrid",
                       drivetrain="hybrid", price=98800, year=2021, engine_cc=1598, depreciation_per_year=11200)
    b = for_used(used, cfg, TODAY, petrol_price=3.48)
    assert b.road_tax == 1483 and b.depreciation == 11200
    assert b.energy == round(150 * 5.5 * 3.48)
    assert b.total_low == 1483 + 1400 + 11200 + b.energy + 3300
    assert b.total_high - b.total_low == 900
    new = NewEvVariant(make="Tesla", model="Model 3", variant="RWD 110", price_with_coe=179999, power_kw=110,
                       range_km=534, listing_url="u", price_source_url="u", source="t")
    nb = for_new_ev(new, cfg, TODAY)
    assert nb.road_tax == 1678 and nb.depreciation == 18000 and nb.label == "Tesla Model 3 RWD 110 (new)"


def test_financing_rules(cfg):
    from costs import financing, loan_to_value, monthly_instalment

    assert loan_to_value(None, cfg) == 0.60
    assert loan_to_value(18000, cfg) == 0.70
    assert loan_to_value(20000, cfg) == 0.70
    assert loan_to_value(20001, cfg) == 0.60
    # 107,999 at 2.48 percent flat over 7 years: 107,999 x 1.1736 / 84.
    assert monthly_instalment(107999, 0.0248, 7) == 1509
    assert monthly_instalment(0, 0.0248, 7) == 0
    f = financing(179999, None, cfg, new_car=True)
    assert f.deposit == 72000 and f.loan == 107999 and f.ltv == 0.60
    assert f.monthly == 1509 and f.tenure_years == 7
    assert f.monthly_short == monthly_instalment(107999, 0.0248, 5) == 2023
    u = financing(98800, 22540, cfg, new_car=False)
    assert u.deposit == 39520 and u.rate_flat == 0.0278
    cheap = financing(60000, 15000, cfg, new_car=False)
    assert cheap.deposit == 18000 and cheap.loan == 42000
