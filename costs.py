"""Annual cost of ownership. Every constant comes from the costs block of config.yaml.

Road tax formulas are published by LTA per 6 months, so the annual figure is the formula
scaled by 12 over period_months. Depreciation uses the PARF schedule that applies to the car's
registration date. Energy uses the daily scraped petrol price when there is one.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from models import CostBreakdown, Drivetrain, NewEvVariant, UsedListing


def _band_amount(value: float, bands: list[dict[str, Any]]) -> float:
    prev_bound = 0.0
    for band in bands:
        upper = band["up_to"]
        if upper is None or value <= upper:
            lower = prev_bound
            return band["base"] + band["rate"] * max(0.0, value - lower)
        prev_bound = upper
    raise ValueError("bands must end with an open band")


def _annualise(amount_per_period: float, cfg: dict[str, Any]) -> float:
    months = cfg["costs"]["road_tax"].get("period_months", 12)
    return amount_per_period * 12 / months


def road_tax_ev(power_kw: float, cfg: dict[str, Any]) -> int:
    rt = cfg["costs"]["road_tax"]
    base = _band_amount(power_kw, rt["ev_power_bands"]) * rt["ev_factor"]
    return int(round(_annualise(base, cfg) + rt["ev_additional_flat_component"]))


def road_tax_petrol(engine_cc: float, cfg: dict[str, Any]) -> int:
    rt = cfg["costs"]["road_tax"]
    base = _band_amount(engine_cc, rt["petrol_cc_bands"]) * rt["petrol_factor"]
    return int(round(_annualise(base, cfg)))


def road_tax_hybrid(engine_cc: float | None, power_kw: float | None, cfg: dict[str, Any]) -> int:
    rt = cfg["costs"]["road_tax"]
    by_engine = road_tax_petrol(engine_cc, cfg) if engine_cc else 0
    by_power = 0
    if power_kw and rt.get("hybrid_rule") == "max_of_engine_and_power":
        by_power = int(round(_annualise(_band_amount(power_kw, rt["ev_power_bands"]) * rt["ev_factor"], cfg)))
    return max(by_engine, by_power)


def road_tax(drivetrain: Drivetrain, engine_cc: float | None, power_kw: float | None, cfg: dict[str, Any]) -> Optional[int]:
    if drivetrain == Drivetrain.ev:
        return road_tax_ev(power_kw, cfg) if power_kw else None
    if drivetrain == Drivetrain.hybrid:
        return road_tax_hybrid(engine_cc, power_kw, cfg) if (engine_cc or power_kw) else None
    return road_tax_petrol(engine_cc, cfg) if engine_cc else None


def arf_from_omv(omv: float, cfg: dict[str, Any]) -> int:
    """Tiered ARF estimate before any EV rebate."""
    total = 0.0
    prev = 0.0
    for tier in cfg["costs"]["depreciation"]["arf_tiers"]:
        upper = tier["omv_up_to"]
        slice_top = omv if upper is None else min(omv, upper)
        if slice_top > prev:
            total += (slice_top - prev) * tier["percent"] / 100
        if upper is None or omv <= upper:
            break
        prev = upper
    return int(round(total))


def parf_schedule_for(reg_date: date | None, cfg: dict[str, Any]) -> dict[str, Any]:
    schedules = cfg["costs"]["depreciation"]["parf_schedules"]
    if reg_date is None:
        return schedules[-1]
    for schedule in schedules:
        if reg_date >= date.fromisoformat(str(schedule["applies_from"])):
            return schedule
    return schedules[-1]


def parf_rebate(arf: float, age_at_dereg: float, reg_date: date | None, cfg: dict[str, Any]) -> int:
    """PARF rebate in SGD for a car deregistered at the given age. Zero after 10 years."""
    schedule = parf_schedule_for(reg_date, cfg)
    percent = 0
    for band in schedule["percent_by_age"]:
        if age_at_dereg <= band["age_under"]:
            percent = band["percent"]
            break
    rebate = arf * percent / 100
    cap = schedule.get("cap_sgd")
    if cap is not None:
        rebate = min(rebate, cap)
    return int(round(rebate))


def _age_years(listing: UsedListing, today: date) -> Optional[float]:
    if listing.reg_date:
        return (today - listing.reg_date).days / 365.25
    if listing.year:
        return today.year - listing.year + 0.5
    return None


def depreciation_used(listing: UsedListing, cfg: dict[str, Any], today: date) -> Optional[int]:
    """(asking price minus PARF rebate at the end of the COE) divided by COE years remaining."""
    years = listing.coe_years_remaining
    if not years or years <= 0:
        return None
    if listing.dereg_value is not None:
        residual = listing.dereg_value
    else:
        arf = listing.arf
        if arf is None and listing.omv:
            arf = arf_from_omv(listing.omv, cfg)
        age_now = _age_years(listing, today)
        if arf is None or age_now is None:
            residual = 0
        else:
            residual = parf_rebate(arf, age_now + years, listing.reg_date, cfg)
    return int(round((listing.price - residual) / years))


def depreciation_new(price_with_coe: float, arf: float | None, cfg: dict[str, Any], today: date) -> int:
    horizon = cfg["costs"]["depreciation"]["new_car_horizon_years"]
    residual = parf_rebate(arf, horizon, today, cfg) if arf else 0
    return int(round((price_with_coe - residual) / horizon))


def energy_cost(drivetrain: Drivetrain, cfg: dict[str, Any], petrol_price: float | None = None) -> int:
    e = cfg["costs"]["energy"]
    km = cfg["costs"]["annual_km"]
    if drivetrain == Drivetrain.ev:
        ev = e["ev"]
        per_kwh = ev["home_share"] * ev["home_price_per_kwh"] + (1 - ev["home_share"]) * ev["public_price_per_kwh"]
        return int(round(km / 100 * ev["kwh_per_100km"] * per_kwh))
    price = petrol_price or e["ice"]["fallback_petrol_95_price"]
    litres = e["hybrid"]["litres_per_100km"] if drivetrain == Drivetrain.hybrid else e["ice"]["litres_per_100km"]
    return int(round(km / 100 * litres * price))


def insurance_range(price: float, drivetrain: Drivetrain, cfg: dict[str, Any]) -> tuple[int, int]:
    for band in cfg["costs"]["insurance"]["bands"]:
        upper = band["price_up_to"]
        if upper is None or price <= upper:
            low, high = band[drivetrain.value]
            return int(low), int(high)
    raise ValueError("insurance bands must end with an open band")


def fixed_extras(drivetrain: Drivetrain, cfg: dict[str, Any]) -> int:
    f = cfg["costs"]["fixed_extras"]
    return int(f["servicing"][drivetrain.value] + f["tyres"] + f["season_parking"] + f["erp"])


def breakdown(label: str, drivetrain: Drivetrain, price: float, engine_cc: float | None, power_kw: float | None,
              depreciation: int, cfg: dict[str, Any], petrol_price: float | None = None) -> CostBreakdown:
    low, high = insurance_range(price, drivetrain, cfg)
    return CostBreakdown(
        label=label,
        drivetrain=drivetrain,
        road_tax=road_tax(drivetrain, engine_cc, power_kw, cfg) or 0,
        insurance_low=low,
        insurance_high=high,
        depreciation=depreciation,
        energy=energy_cost(drivetrain, cfg, petrol_price),
        fixed_extras=fixed_extras(drivetrain, cfg),
    )


def for_used(listing: UsedListing, cfg: dict[str, Any], today: date, petrol_price: float | None = None) -> CostBreakdown:
    dep = listing.depreciation_per_year
    if dep is None:
        dep = depreciation_used(listing, cfg, today) or 0
    label = f"{listing.display_name} {listing.year or ''}".strip()
    return breakdown(label, listing.drivetrain, listing.price, listing.engine_cc, listing.power_kw, dep, cfg, petrol_price)


def for_new_ev(variant: NewEvVariant, cfg: dict[str, Any], today: date, arf: float | None = None) -> CostBreakdown:
    price = variant.price_with_coe or 0
    dep = depreciation_new(price, arf, cfg, today)
    return breakdown(f"{variant.display_name} (new)", Drivetrain.ev, price, None, variant.power_kw, dep, cfg)
