"""Website data: everything the tracker knows today, written to data/site/data.json for the car-site
container (nginx, site/index.html). main.py rewrites it after every full report; `python website.py`
rebuilds it from the database and the day's page cache without scraping anything new.

The Telegram report shows a shortlist (top 20, filtered). The website shows every new EV variant on
today's price list and every live used listing, with the same costings on each card, so it can be
browsed by body type, budget, brand and drivetrain. Nothing here changes the report.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import costs
from coe_forecast import bidding_window, forecast
from db import Database
from filters import brand_rank, reject_reason, shortlist
from scrapers.used_common import is_commercial_model
from models import CoeResult, Drivetrain, NewCarVariant, NewEvVariant, UsedListing
from scrapers.coe import next_tender_date
from scrapers.motorbikes import scrape_motorbikes, shortlist as shortlist_bikes
from scrapers.new_ev import BODY_TYPES, best_selling_by_body_type, make_matches, model_prices, top_best_seller
from scrapers.registrations import scrape_body_types, scrape_registrations

log = logging.getLogger(__name__)

DEFAULT_OUTPUT = "data/site/data.json"


def _norm(text: str) -> str:
    """Lower case, hyphens and dots as spaces, one space between words: "Mercedes-Benz e-C4 X" and
    "MERCEDES BENZ E C4 X" compare equal."""
    return " ".join(re.sub(r"[^a-z0-9#+]+", " ", text.lower()).split())


def body_type(make: str, model: str, variant: str, cfg: dict[str, Any]) -> str:
    """Body type from config.yaml `new_ev.body_type_overrides` (longest key that opens "make model"
    wins), else from a body word in the variant text, else Other."""
    name = _norm(f"{make} {model}")
    best = ""
    for key, value in cfg["new_ev"].get("body_type_overrides", {}).items():
        k = _norm(str(key))
        if (name == k or name.startswith(k + " ")) and len(k) > len(best):
            best, found = k, value
    if best:
        return found
    low = f"{model} {variant}".lower()
    for needle, value in (("cabrio", "Coupe"), ("convertible", "Coupe"), ("spider", "Coupe"), ("spyder", "Coupe"),
                          ("roadster", "Coupe"), ("touring", "Wagon"), ("avant", "Wagon")) + BODY_TYPES:
        if needle in low:
            return value
    return "Other"


def _fin(price: Optional[int], omv: Optional[int], cfg: dict[str, Any], new_car: bool) -> dict[str, Any]:
    if not price:
        return {}
    f = costs.financing(price, omv, cfg, new_car)
    return {"deposit": f.deposit, "loan": f.loan, "monthly": f.monthly, "tenure": f.tenure_years,
            "monthly_short": f.monthly_short, "tenure_short": f.short_tenure_years}


def _annual(breakdown) -> dict[str, int]:
    return {"road_tax": breakdown.road_tax, "insurance_low": breakdown.insurance_low, "insurance_high": breakdown.insurance_high,
            "depreciation": breakdown.depreciation, "energy": breakdown.energy, "extras": breakdown.fixed_extras,
            "total_low": breakdown.total_low, "total_high": breakdown.total_high}


def _rank_in(make: str, counts: dict[str, int] | None, cfg: dict[str, Any]) -> tuple[Optional[int], int]:
    """(rank, registrations) of a brand in an LTA count table, matching LTA's spelling. A price list
    make sold under another LTA make (Aion by GAC, Proton by Geely) counts as that make, `site.make_aliases`."""
    if not counts:
        return None, 0
    lta_make = cfg.get("site", {}).get("make_aliases", {}).get(make, make)
    n = max((v for m, v in counts.items() if make_matches(m, lta_make)), default=0)
    return (brand_rank(lta_make, counts) if n else None), n


def new_ev_cards(variants: list[NewEvVariant], cfg: dict[str, Any], today: date, ev_sales: dict[str, int] | None,
                 body_counts: dict[str, dict[str, int]] | None, pop: "Popularity | None" = None) -> list[dict[str, Any]]:
    out = []
    goods = cfg["used"]["filters"].get("exclude_models", []) + cfg.get("new_cars", {}).get("exclude_models", [])
    for i, v in enumerate(variants):
        if is_commercial_model(f"{v.make} {v.model}", goods):   # the EV price list carries electric vans too
            continue
        body = body_type(v.make, v.model, v.variant, cfg)
        rank, regs = _rank_in(v.make, ev_sales, cfg)
        body_rank, body_regs = _rank_in(v.make, (body_counts or {}).get(body), cfg)
        card: dict[str, Any] = {
            "id": f"new-{i}", "make": v.make, "model": v.model, "variant": v.variant, "name": v.display_name,
            "price": v.price_with_coe, "price_next": list(v.price_forecast) if v.price_forecast else None,
            "cat": v.coe_category.value if v.coe_category else None, "body": body,
            "range_km": v.range_km, "range_standard": v.range_standard, "battery_kwh": v.battery_kwh,
            "power_kw": v.power_kw, "ves_rebate": v.ves_rebate, "vehicle_warranty": v.vehicle_warranty,
            "battery_warranty": v.battery_warranty, "promotion": v.promotion, "url": v.listing_url,
            "score": round(v.score, 1) if v.score else None,
            "brand_rank": rank, "brand_regs": regs, "body_rank": body_rank, "body_regs": body_regs,
            "popularity": pop.of(v.make, v.model, ev=True) if pop else None,
            "best_seller": pop.best(v.make, v.model, ev=True) if pop else None,
            "finance": _fin(v.price_with_coe, None, cfg, True),
        }
        if v.price_with_coe:
            try:
                card["annual"] = _annual(costs.for_new_ev(v, cfg, today))
            except Exception as exc:  # a variant with no power rating has no road tax formula
                log.debug("no cost estimate for %s: %s", v.display_name, exc)
        out.append(card)
    return out


FUEL_WORDS = re.compile(r"\b(?:plug in hybrid|mild hybrid|m hybrid|hybrid|electric|ev|e tron)\b")
# Body spellings the used sites abbreviate ("Mazda 3 4DR" is the Mazda 3 Sedan).
BODY_SPELLINGS = {"4dr": "sedan", "5dr": "hatchback", "hb": "hatchback", "saloon": "sedan", "hatch": "hatchback"}
# Words that only name a body style. "Audi Q4" may take the rank of "Audi Q4 Sportback"; "Yaris" never takes
# "Yaris Cross", "EQS" never takes "EQS SUV" and "Honda N" never takes "N-Box", because those words make
# another model.
BODY_WORDS = {"sedan", "sportback", "hatchback", "coupe", "gran", "touring", "avant", "cabriolet", "convertible",
              "wagon", "estate", "limousine", "lwb"}
# Make prefixes the used sites put in front of the brand Sgcarmart lists ("GREAT WALL ORA Good Cat").
MAKE_PREFIXES = ("great wall ",)


def _model_key(make: str, model: str) -> str:
    """One key per model family however a site spells it: "BYD Sealion 7 Electric" and "BYD SEALION 7",
    "MG S6 EV" and "MG S6", "Mazda 3 4DR" and "Mazda 3 Sedan". Fuel words go, the model's own name stays
    ("Kia EV5" keeps "ev5")."""
    words = [BODY_SPELLINGS.get(w, w) for w in FUEL_WORDS.sub(" ", _norm(f"{make} {model}")).split()]
    key = " ".join(words)
    for prefix in MAKE_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
    return key


class Popularity:
    """Sgcarmart's popularity rank, and its published best seller position, by model, looked up however
    each site spells the name.

    Ranks are kept apart by fuel, because Sgcarmart lists "CLA Electric" (92) and "CLA Hybrid" (78) as two
    cars: an EV card looks among the electric listings first and a petrol card among the rest, and only
    falls back to the other fuel when its own has no such model (the Kona EV has only "Kona Hybrid").

    For each pool: the exact model; else the longest ranked name the card's name starts with, on whole words
    ("Atto 3 Electric Sunroof" is "Atto 3", "Seal 6" never takes "Seal"); else, for a shorter name, the best
    body style of it ("Audi A6" is "A6 Sportback e-tron"), only when the extra words name a body."""

    def __init__(self, cars: list[NewCarVariant]):
        self.pools: dict[str, dict[str, dict[str, Any]]] = {"ev": {}, "other": {}}
        for v in cars:
            if not v.popularity and not v.best_list:
                continue
            k = _model_key(v.make, v.model)
            e = self.pools["ev" if v.fuel == "ev" else "other"].setdefault(k, {"rank": None, "best": None})
            if v.popularity:
                e["rank"] = min(e["rank"] or v.popularity, v.popularity)
            if v.best_list and (e["best"] is None or v.best_pos < e["best"][1]):
                e["best"] = (v.best_list, v.best_pos)
        self._keys = {p: sorted(d, key=len, reverse=True) for p, d in self.pools.items()}

    def _find(self, pool: str, k: str, make: str) -> Optional[dict[str, Any]]:
        d, keys = self.pools[pool], self._keys[pool]
        if k in d:
            return d[k]
        hit = next((key for key in keys if k.startswith(key + " ")), None)
        if hit:
            return d[hit]
        if len(k.split()) > len(_norm(make).split()):   # a bare make never takes a rank
            bodies = [d[key] for key in keys if key.startswith(k + " ") and set(key[len(k) + 1:].split()) <= BODY_WORDS]
            if bodies:
                return min(bodies, key=lambda e: e["rank"] or 10**6)
        return None

    def entry(self, make: str, model: str, ev: Optional[bool] = None) -> Optional[dict[str, Any]]:
        k = _model_key(make, model)
        order = ["ev", "other"] if ev else ["other", "ev"] if ev is False else ["ev", "other"]
        found = [e for e in (self._find(p, k, make) for p in order) if e]
        if ev is None and len(found) == 2:   # fuel unknown: the better of the two
            return min(found, key=lambda e: e["rank"] or 10**6)
        return found[0] if found else None

    def of(self, make: str, model: str, ev: Optional[bool] = None) -> Optional[int]:
        e = self.entry(make, model, ev)
        return e["rank"] if e else None

    def best(self, make: str, model: str, ev: Optional[bool] = None) -> Optional[dict[str, Any]]:
        """Sgcarmart's published best seller slot, {"list": "Cat A", "pos": 3}, or None."""
        e = self.entry(make, model, ev)
        return {"list": e["best"][0], "pos": e["best"][1]} if e and e["best"] else None


def coe_cat_for(v: NewCarVariant, cfg: dict[str, Any]) -> Optional[str]:
    """Cat A for an engine up to 1,600 cc and 97 kW (or an EV up to 110 kW), else Cat B. None when unknown."""
    c = cfg["coe"]
    if v.fuel == "ev":
        return None if v.power_kw is None else ("A" if v.power_kw <= c["cat_a_max_power_kw"] else "B")
    if v.engine_cc is None:
        return None
    return "A" if v.engine_cc <= c["cat_a_max_engine_cc"] and (v.power_kw or 0) <= c["cat_a_max_engine_power_kw"] else "B"


def same_car(listed: NewCarVariant, ev_list: list[NewEvVariant]) -> bool:
    """True when a listing EV is already on the EV price list. The two name a car differently ("Audi Q4" and
    "Audi Q4 Sportback e-tron Electric"), so it is the same make, one name a whole word prefix of the other,
    and a variant at the same price. "Ioniq 5 N" stays: no EV list variant has its price."""
    k = _model_key(listed.make, listed.model)
    for v in ev_list:
        e = _model_key(v.make, v.model)
        if _norm(v.make) != _norm(listed.make) or v.price_with_coe != listed.price_with_coe:
            continue
        if e == k or k.startswith(e + " ") or e.startswith(k + " "):
            return True
    return False


def new_car_cards(variants: list[NewCarVariant], ev_list: list[NewEvVariant], cfg: dict[str, Any], today: date,
                  petrol_price: Optional[float], sales: dict[str, dict[str, int] | None],
                  deltas: dict[str, tuple[int, int, int]], pop: "Popularity | None" = None) -> list[dict[str, Any]]:
    """Petrol, hybrid and diesel cars from the full listing, plus EVs the EV price list does not carry."""
    out = []
    ev_models = {_model_key(v.make, v.model) for v in ev_list}
    for i, v in enumerate(variants):
        if v.fuel == "ev" and (_model_key(v.make, v.model) in ev_models or same_car(v, ev_list)):
            continue
        kind = "ev" if v.fuel == "ev" else "ice"
        dt = Drivetrain.ev if v.fuel == "ev" else Drivetrain.hybrid if v.fuel == "hybrid" else Drivetrain.ice
        rank, regs = _rank_in(v.make, sales.get(kind), cfg)
        cat = coe_cat_for(v, cfg)
        d = deltas.get(cat) if cat else None
        card: dict[str, Any] = {
            "id": f"car-{i}", "make": v.make, "model": v.model, "variant": v.variant, "name": v.display_name,
            "fuel": v.fuel, "price": v.price_with_coe, "cat": cat, "body": body_type(v.make, v.model, v.variant, cfg),
            "price_next": [v.price_with_coe + x for x in d] if d else None,
            "power_kw": v.power_kw, "engine_cc": v.engine_cc, "economy": v.fuel_economy, "transmission": v.transmission,
            "price_change": v.price_change, "price_change_on": v.price_change_on, "url": v.listing_url,
            # A dealer special edition sits only in a paid spotlight slot, so it has no rank of its own; it
            # takes its model's ("WR-V Vin's Auto Edition" is a WR-V).
            "brand_rank": rank, "brand_regs": regs,
            "popularity": v.popularity or (pop.of(v.make, v.model, ev=v.fuel == "ev") if pop else None),
            # only a car with no rank of its own borrows its model's best seller slot, so "GLB Hybrid" (550) never
            # wears the "GLB Mild Hybrid" Cat A badge
            "best_seller": ({"list": v.best_list, "pos": v.best_pos} if v.best_list
                            else None if v.popularity else (pop.best(v.make, v.model, ev=v.fuel == "ev") if pop else None)),
            "finance": _fin(v.price_with_coe, None, cfg, True),
        }
        try:
            b = costs.breakdown(v.display_name, dt, v.price_with_coe, v.engine_cc, v.power_kw,
                                costs.depreciation_new(v.price_with_coe, None, cfg, today), cfg, petrol_price)
            card["annual"] = _annual(b)
        except Exception as exc:
            log.debug("no cost estimate for %s: %s", v.display_name, exc)
        out.append(card)
    return out


def _price_change(l: UsedListing) -> tuple[Optional[int], Optional[str]]:
    """(previous different price, date it was last seen) or (None, None) when the price never moved."""
    for p in reversed(l.price_history):
        if p.price != l.price:
            return p.price, p.seen_on.isoformat()
    return None, None


def _changes(l: UsedListing) -> list[dict[str, Any]]:
    """Price history with the flat days removed: first sighting, every change, today."""
    out: list[dict[str, Any]] = []
    for p in l.price_history:
        if not out or out[-1]["price"] != p.price:
            out.append({"on": p.seen_on.isoformat(), "price": p.price})
    if l.price_history and out[-1]["on"] != l.price_history[-1].seen_on.isoformat():
        out.append({"on": l.price_history[-1].seen_on.isoformat(), "price": l.price_history[-1].price})
    return out


def _used_rank(pop: "Popularity | None", l: UsedListing) -> Optional[int]:
    """The used car's model on Sgcarmart's new car list. The model alone first ("Mazda 3" is the best of its
    bodies), then with the variant, which carries the rest of the name when a site splits it oddly
    ("GREAT" "WALL" "ORA GOOD CAT", "Mazda" "3 4DR" "1.5 AT")."""
    if not pop:
        return None
    ev = l.drivetrain == Drivetrain.ev
    return pop.of(l.make, l.model, ev=ev) or pop.of(l.make, f"{l.model} {l.variant}", ev=ev)


def used_cards(listings: list[UsedListing], cfg: dict[str, Any], today: date, petrol_price: Optional[float],
               sales: dict[str, dict[str, int] | None], pop: "Popularity | None" = None) -> list[dict[str, Any]]:
    out = []
    for l in listings:
        if l.depreciation_per_year is None:
            l.depreciation_per_year = costs.depreciation_used(l, cfg, today)
        kind = "ev" if l.drivetrain == Drivetrain.ev else "ice"
        rank, regs = _rank_in(l.make, sales.get(kind), cfg)
        prev, prev_on = _price_change(l)
        reason = reject_reason(l, cfg, today)
        card: dict[str, Any] = {
            "id": f"{l.source}-{l.listing_id}", "source": l.source, "url": l.url,
            "make": l.make, "model": l.model, "variant": l.variant, "name": l.display_name,
            "drivetrain": l.drivetrain.value, "body": body_type(l.make, l.model, l.variant, cfg),
            "year": l.year, "reg_date": l.reg_date.isoformat() if l.reg_date else None,
            "mileage_km": l.mileage_km, "owners": l.owners, "price": l.price,
            "dep_year": l.depreciation_per_year, "coe_left": l.coe_years_remaining,
            "coe_expiry": l.coe_expiry.isoformat() if l.coe_expiry else None,
            "omv": l.omv, "arf": l.arf, "dereg_value": l.dereg_value, "engine_cc": l.engine_cc, "power_kw": l.power_kw,
            "flags": l.flags, "first_seen": l.first_seen.isoformat() if l.first_seen else None,
            "days_listed": (today - l.first_seen).days if l.first_seen else None,
            "prev_price": prev, "prev_price_on": prev_on,
            "history": _changes(l),
            "passes": reason is None, "reject_reason": reason,
            "brand_rank": rank, "brand_regs": regs, "popularity": _used_rank(pop, l),
            "finance": _fin(l.price, l.omv, cfg, False),
        }
        try:
            card["annual"] = _annual(costs.for_used(l, cfg, today, petrol_price))
        except Exception as exc:
            log.debug("no cost estimate for %s: %s", l.display_name, exc)
        out.append(card)
    return out


def coe_block(db: Database, cfg: dict[str, Any], today: date, tenders: int) -> dict[str, Any]:
    latest = db.latest_coe()
    if not latest:
        return {}
    coe = cfg["coe"]
    fc = coe.get("forecast", {})
    forecasts = {}
    if fc.get("enabled"):
        for r in latest:
            f = forecast(db.coe_history(r.category.value, fc["window"] + 1), fc)
            if f:
                forecasts[f.category] = f
    rows = []
    for r in latest:
        if r.category.value not in coe["display_categories"]:
            continue
        history = list(reversed(db.coe_history(r.category.value, tenders)))
        prev = history[-2].quota_premium if len(history) >= 2 else None
        f = forecasts.get(r.category.value)
        rows.append({
            "cat": r.category.value, "premium": r.quota_premium,
            "delta": r.quota_premium - prev if prev else None,
            "delta_pct": round((r.quota_premium - prev) / prev * 100, 2) if prev else None,
            "bids": r.bids_received, "quota": r.quota, "successful": r.bids_successful,
            "history": [{"on": h.tender_date.isoformat(), "premium": h.quota_premium} for h in history],
            "forecast": {"point": f.point, "low": f.low, "high": f.high} if f else None,
        })
    weeks = tuple(coe["tender_weeks_of_month"])
    nxt = next_tender_date(latest[0].tender_date, weeks, coe["results_weekday"])
    if nxt < today:
        nxt = next_tender_date(today - timedelta(days=1), weeks, coe["results_weekday"])
    opens, closes = bidding_window(nxt, cfg)
    watch = coe.get("watch", {})
    horizon = today + timedelta(days=int(watch.get("horizon_days", 60)))
    events = sorted((date.fromisoformat(str(e["date"])), e["text"]) for e in watch.get("events", []))
    return {
        "tender_date": latest[0].tender_date.isoformat(), "exercise": latest[0].exercise, "source": latest[0].source,
        "next_results": nxt.isoformat(), "bidding_opens": opens.isoformat(timespec="minutes"),
        "bidding_closes": closes.isoformat(timespec="minutes"),
        "events": [{"on": d.isoformat(), "text": t} for d, t in events if today <= d <= horizon],
        "rows": rows, "forecast_note": fc.get("enabled", False),
    }


def brands_block(reg, ev_models: dict[str, Any], prices: dict[str, dict[str, int]], cfg: dict[str, Any],
                 top_n: int, pop: "Popularity | None" = None) -> dict[str, Any]:
    if not reg:
        return {}
    makes, months = reg
    from scrapers.new_ev import lineup

    def ranked(kind: str) -> list[dict[str, Any]]:
        pool = sum(m.get(kind, 0) for m in makes.values()) or 1
        rows = sorted(((k, m[kind]) for k, m in makes.items() if m.get(kind)), key=lambda t: -t[1])
        return [{"rank": n, "make": make, "regs": count, "share": round(count * 100 / pool, 1)}
                for n, (make, count) in enumerate(rows, start=1)]

    ev = ranked("ev")
    for row in ev:
        models = []
        for model in lineup(ev_models.get(row["make"])):
            price = prices.get(row["make"], {}).get(model)
            rank = pop.of(row["make"], model, ev=True) if pop else None
            if rank is None and pop:   # the line up names the LTA make; the price list may use a sub brand
                rank = next((r for alias, lta in cfg.get("site", {}).get("make_aliases", {}).items()
                             if lta.lower() == row["make"].lower() for r in [pop.of(alias, model, ev=True)] if r), None)
            models.append({"name": model, "price": price, "finance": _fin(price, None, cfg, True), "popularity": rank})
        # Sgcarmart's live popularity order; models it does not rank keep the hand kept order after them.
        row["models"] = sorted(models, key=lambda m: m["popularity"] or 10**6)
    return {"months": months, "total": sum(m["total"] for m in makes.values()),
            "ev_total": sum(m.get("ev", 0) for m in makes.values()),
            "petrol_total": sum(m.get("petrol", 0) for m in makes.values()),
            "ev": ev, "petrol": ranked("petrol")[:top_n], "models_checked_on": str(cfg.get("top_sellers", {}).get("ev_models_checked_on", ""))}


def bikes_block(bikes: dict[str, Any], cfg: dict[str, Any], today: date) -> dict[str, Any]:
    m = cfg["motorbikes"]
    fin = m["financing"]
    classes = {}
    for cls, found in (bikes.get("used") or {}).items():
        kept = {b.url for b in shortlist_bikes(found, cfg, today)}
        rows, seen = [], set()
        for b in found:
            if b.url in seen:
                continue
            seen.add(b.url)
            left = b.coe_years_left(today)
            deposit = int(round(b.price * fin["deposit_pct"]))
            years = min(int(fin["tenure_years"]), int(left or 0)) or 1
            rows.append({
                "title": b.title, "url": b.url, "price": b.price, "year": b.reg_date.year if b.reg_date else None,
                "cc": b.cc, "type": b.bike_type, "mileage_km": b.mileage_km, "electric": b.electric,
                "coe_left": left, "dep_year": b.depreciation(today), "passes": b.url in kept,
                "finance": {"deposit": deposit, "monthly": costs.monthly_instalment(b.price - deposit, fin["flat_rate"], years), "tenure": years},
            })
        rows.sort(key=lambda r: (not r["passes"], r["dep_year"] or 10**9))
        classes[cls] = rows
    brands = []
    months: list[str] = []
    if bikes.get("brands"):
        makes, months = bikes["brands"]
        pool = sum(v["total"] for v in makes.values()) or 1
        for n, (make, v) in enumerate(sorted(makes.items(), key=lambda t: -t[1]["total"])[: m.get("top_n_brands", 15)], start=1):
            brands.append({"rank": n, "make": make, "regs": v["total"], "share": round(v["total"] * 100 / pool, 1), "electric": v.get("ev", 0)})
    return {"classes": classes, "brands": brands, "months": list(months), "filters": m["filters"], "financing": fin}


def build(cfg: dict[str, Any], db: Database, today: date, user_agent: str, force: bool = False) -> dict[str, Any]:
    """Everything the website shows, as plain JSON types. Scrapers are only used for the LTA tables and
    SGBikemart, which come from the day's cache after a report run."""
    site_cfg = cfg.get("site", {})
    reg = scrape_registrations(cfg, user_agent, today, force)
    makes = reg[0] if reg else {}
    sales = {"ev": {m: v.get("ev", 0) for m, v in makes.items()} or None,
             "ice": {m: v.get("petrol", 0) for m, v in makes.items()} or None}
    body = scrape_body_types(cfg, user_agent, today, force)
    body_counts, body_months = body if body else (None, [])

    fuel = db.latest_fuel_price()
    petrol_price = fuel.ron95_per_litre if fuel else None

    coe = coe_block(db, cfg, today, int(site_cfg.get("coe_history_tenders", 12)))
    forecasts = {r["cat"]: r["forecast"] for r in coe.get("rows", []) if r.get("forecast")}

    stored = db.new_ev_on(today)
    if not stored:  # the site also builds on a day with no run yet: show the last price list
        row = db.conn.execute("SELECT MAX(scraped_on) AS d FROM new_ev_prices").fetchone()
        stored = db.new_ev_on(date.fromisoformat(row["d"])) if row and row["d"] else []
    for v in stored:
        f = forecasts.get(v.coe_category.value) if v.coe_category else None
        if f and v.price_with_coe:
            last = next(r["premium"] for r in coe["rows"] if r["cat"] == v.coe_category.value)
            v.price_forecast = tuple(v.price_with_coe + x - last for x in (f["low"], f["point"], f["high"]))  # type: ignore[assignment]
        v.body_type = body_type(v.make, v.model, v.variant, cfg)
    new_evs = sorted(stored, key=lambda v: (v.make.lower(), v.model.lower(), v.price_with_coe or 0))

    ndate = db.latest_new_cars_date()
    cars = sorted(db.new_cars_on(ndate) if ndate else [], key=lambda v: (v.make.lower(), v.model.lower(), v.price_with_coe))
    pop = Popularity(cars)
    listings = db.active_listings()
    used = used_cards(listings, cfg, today, petrol_price, sales, pop)

    # The full new car list: petrol and hybrid cars, and any EV the EV price list missed. The deltas move a
    # price with the forecast COE premium, like the EV cards.
    deltas = {r["cat"]: tuple(r["forecast"][k] - r["premium"] for k in ("low", "point", "high"))
              for r in coe.get("rows", []) if r.get("forecast")}
    ev_cards = new_ev_cards(new_evs, cfg, today, sales["ev"], body_counts, pop)
    new_cars = new_car_cards(cars, stored, cfg, today, petrol_price, sales, deltas, pop)

    # The report's three cost picks, with the same choices: best selling new EV, then the first car of
    # each used shortlist.
    picks = []
    best_new = None
    if stored and body_counts:
        best_new = top_best_seller(best_selling_by_body_type(stored, body_counts, cfg))
    if best_new is None and stored:
        scored = [v for v in stored if v.score is not None]
        best_new = min(scored, key=lambda v: v.score) if scored else None
    if best_new is not None:
        try:
            picks.append({"label": "New EV", "name": best_new.display_name, "url": best_new.listing_url, "drivetrain": "ev",
                          **_annual(costs.for_new_ev(best_new, cfg, today)), "finance": _fin(best_new.price_with_coe, None, cfg, True)})
        except Exception as exc:
            log.debug("no cost pick for %s: %s", best_new.display_name, exc)
    evs = [l for l in listings if l.drivetrain == Drivetrain.ev]
    others = [l for l in listings if l.drivetrain != Drivetrain.ev]
    for label, pool, kind in (("Used EV", evs, "ev"), ("Used ICE", others, "ice")):
        top, _ = shortlist(pool, cfg, today, brand_sales=sales[kind])
        if top:
            l = top[0]
            picks.append({"label": label, "name": f"{l.display_name} {l.year or ''}".strip(), "url": l.url, "drivetrain": l.drivetrain.value,
                          **_annual(costs.for_used(l, cfg, today, petrol_price)), "finance": _fin(l.price, l.omv, cfg, False)})

    ts = cfg.get("top_sellers", {})
    bikes = scrape_motorbikes(cfg, user_agent, today, force)
    bc = cfg["buying_considerations"]
    f = cfg["used"]["filters"]
    fin = cfg["costs"]["financing"]
    yesterday = today - timedelta(days=1)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"), "run_date": today.isoformat(),
        "stats": {
            "new_ev_variants": len(ev_cards), "new_ev_brands": len({c["make"] for c in ev_cards}),
            "new_car_variants": len(new_cars), "new_car_models": len({(c["make"], c["model"]) for c in new_cars}),
            "used_active": len(used), "used_passing": sum(1 for u in used if u["passes"]),
            "new_today": sum(1 for u in used if u["first_seen"] == today.isoformat()),
            "drops": sum(1 for u in used if u["prev_price"] and u["prev_price"] > u["price"]),
            "gone": db.count_gone_since(yesterday),
        },
        "coe": coe,
        "fuel": {"observed_on": fuel.observed_on.isoformat(), "ron95": fuel.ron95_per_litre, "grades": fuel.grades,
                 "by_brand": fuel.by_brand, "source": fuel.source} if fuel else {},
        "new_evs": ev_cards,
        "new_cars": new_cars, "new_cars_on": ndate.isoformat() if ndate else None,
        "used": used,
        "brands": brands_block(reg, ts.get("ev_models") or {}, model_prices(new_evs, ts.get("ev_models") or {}), cfg,
                               int(ts.get("top_n", 20)), pop),
        "body_months": list(body_months),
        "bikes": bikes_block(bikes, cfg, today),
        "costs": {"picks": picks, "assumptions": cfg["costs"]["insurance"]["assumptions"].strip(), "annual_km": cfg["costs"]["annual_km"],
                  "insurance_links": cfg["sources"].get("insurance_comparison", []),
                  "rates": {"new": fin["flat_rate_new"], "used": fin["flat_rate_used"], "tenure": fin["max_tenure_years"], "short": fin["short_tenure_years"]}},
        "filters": {"price_ceiling": f["price_ceiling_sgd"], "max_km_per_year": f["max_km_per_year_of_age"], "max_owners": f["max_owners"],
                    "min_coe_years": f["min_coe_years_remaining"], "max_age": f["max_age_years"]},
        "guide": {"loan_rules": bc["loan_rules"].strip(), "arf_parf": bc["arf_parf"].strip(),
                  "ev_incentives": bc.get("ev_incentives", []), "policy_watch": bc.get("policy_watch", []),
                  "rule_of_thumb": bc["rule_of_thumb"].strip().format(cat_a_threshold=f"${bc['cat_a_threshold_sgd']:,}"),
                  "cat_a_threshold": bc["cat_a_threshold_sgd"], "verified_on": str(bc.get("verified_on", ""))},
        "sources": {k: cfg["sources"][k] for k in ("onemotoring_coe", "motorist_coe", "datagov_coe_page", "lta_tax_structure", "lta_parf",
                                                   "mas_loan_rules", "lta_registrations_by_make", "lta_mc_registrations_by_make",
                                                   "fuel_price", "sgcarmart_new_cars") if k in cfg["sources"]},
    }


def write(data: dict[str, Any], path: Path | str) -> Path:
    """Atomic write (tmp then rename) so nginx never serves a half written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass
    return path


def publish(cfg: dict[str, Any], db: Database, today: date, user_agent: str, force: bool = False) -> Optional[Path]:
    """Build and write the site data. Best effort: a failure is logged and never stops a report."""
    try:
        data = build(cfg, db, today, user_agent, force)
        out = write(data, cfg.get("site", {}).get("output", DEFAULT_OUTPUT))
        log.info("site data written to %s: %d new EVs, %d used listings", out, len(data["new_evs"]), len(data["used"]))
        return out
    except Exception as exc:
        log.error("site data not written: %s", exc, exc_info=True)
        return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    from settings import load_config, user_agent

    p = argparse.ArgumentParser(description="Rebuild the website data from the database and today's page cache")
    p.add_argument("--out", default=None, help="write here instead of config site.output")
    p.add_argument("--config", default=None)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config) if args.config else load_config()
    if args.out:
        cfg.setdefault("site", {})["output"] = args.out
    db = Database(cfg["general"]["db_path"])
    try:
        return 0 if publish(cfg, db, date.today(), user_agent(cfg)) else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
