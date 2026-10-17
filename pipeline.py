"""Daily pipeline: run the scrapers and store what they find. website.py builds the site from the database.

Each scraper is isolated. When one fails its key goes into `unavailable` and the run continues, so a
single site changing layout never blocks the rest. main.py records `unavailable` as the run's health,
which scheduler.py and heal.py read to decide on a retry or a self repair.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import vault
from ai import ClaudeCli
from coe_forecast import forecast
from db import Database
from models import FuelPrice
from scrapers.coe import DataGovCoeScraper, ensure_schedule, scrape_coe
from scrapers.fuel_cnergy import scrape_cnergy
from scrapers.fuel_price import pick_price, scrape_fuel_price
from scrapers.new_cars import scrape_new_cars
from scrapers.new_ev import scrape_new_evs, view360_links
from scrapers.official import scrape_official
from scrapers.registrations import scrape_body_types, scrape_registrations
from scrapers.used_carro import CarroUsedScraper
from scrapers.used_motorist import MotoristUsedScraper
from scrapers.used_sgcarmart import SgcarmartUsedScraper

log = logging.getLogger(__name__)

USED_SCRAPERS = {
    "sgcarmart": SgcarmartUsedScraper,
    "carro": CarroUsedScraper,
    "motorist": MotoristUsedScraper,
}

# Scrape steps per --section, in run order.
SECTION_STEPS = {
    "coe": ("coe",),
    "new": ("new_ev", "new_cars", "official"),
    "used": ("fuel", "used"),
    "all": ("fuel", "coe", "used", "new_ev", "new_cars", "official"),
}


class Pipeline:
    def __init__(self, cfg: dict[str, Any], db: Database, run_date: date, user_agent: str, force: bool = False):
        self.cfg = cfg
        self.db = db
        self.run_date = run_date
        self.ua = user_agent
        self.force = force
        self.unavailable: dict[str, str] = {}
        self.coe_forecasts: dict = {}
        self.ai = ClaudeCli(cfg)

    def run(self, section: str = "all") -> None:
        """Run the scrape steps of one section and store the results."""
        for step in SECTION_STEPS[section]:
            getattr(self, f"run_{step}")()

    # Steps

    def run_fuel(self) -> None:
        fp = scrape_fuel_price(self.cfg, self.ua, self.run_date, self.force)
        ice_cfg = self.cfg["costs"]["energy"]["ice"]
        station = ice_cfg.get("preferred_station")
        board = scrape_cnergy(self.cfg, self.ua, self.run_date, self.force) if station and station.lower() == "cnergy" else {}
        if board:
            if fp is None:
                fp = FuelPrice(observed_on=self.run_date, ron95_per_litre=0.0, source=self.cfg["sources"]["cnergy"])
            fp.station_prices = board
            grade95 = board.get("95", {})
            if grade95:
                use_member = ice_cfg.get("preferred_station_use_member_price", True)
                fp.by_brand.setdefault(station, grade95.get("member" if use_member and "member" in grade95 else "public", grade95.get("public")))
            if fp.by_brand:
                fp.ron95_per_litre = round(pick_price(fp.by_brand, ice_cfg.get("price_pick", "median"), ice_cfg.get("price_brand")), 2)
        elif station:
            # Not a failure worth flagging daily: Cnergy stopped publishing prices online in 2026.
            log.info("no %s price board today", station)
        if fp and fp.ron95_per_litre:
            self.db.upsert_fuel_price(fp)

    def run_coe(self) -> None:
        ensure_schedule(self.cfg, self.ua, self.run_date)
        try:
            results = scrape_coe(self.cfg, self.ua, self.run_date, self.force)
            self.db.upsert_coe_results(results)
        except Exception as exc:
            log.error("COE scrape failed: %s", exc)
            self.unavailable["coe"] = str(exc)
        self.backfill_coe_history()
        latest = self.db.latest_coe()
        self.make_coe_forecasts(latest)
        if vault.enabled():
            for r in latest:
                hist = self.db.coe_history(r.category.value, 2)
                vault.coe_result(r, r.quota_premium - hist[1].quota_premium if len(hist) == 2 else None)

    def backfill_coe_history(self) -> None:
        """Official history from data.gov.sg for tenders not stored yet, so the forecast has a window to work on."""
        fc = self.cfg["coe"].get("forecast", {})
        if not fc.get("enabled"):
            return
        scraper = DataGovCoeScraper(self.cfg, self.ua, self.run_date, self.force)
        try:
            self.db.drop_coe_source(scraper.name)  # rebuilt from the official history, so a date fixed by the schedule never doubles
            have = self.db.coe_keys()
            rows = scraper.history(fc["history_exercises"])
            self.db.upsert_coe_results(r for r in rows if (r.tender_date.isoformat(), r.category.value) not in have)
            self.db.fill_coe_details(r for r in rows if (r.tender_date.isoformat(), r.category.value) in have)
        except Exception as exc:
            log.warning("COE history backfill failed: %s", exc)
        finally:
            scraper.close()

    def make_coe_forecasts(self, latest: list) -> None:
        fc = self.cfg["coe"].get("forecast", {})
        self.coe_forecasts = {}
        if not fc.get("enabled"):
            return
        for r in latest:
            f = forecast(self.db.coe_history(r.category.value, fc["window"] + 1), fc)
            if f:
                self.coe_forecasts[f.category] = f
        if self.coe_forecasts:
            vault.event("🔮", "coe forecast", ", ".join(f"Cat {c} {f.point:,} ({f.low:,} to {f.high:,})" for c, f in sorted(self.coe_forecasts.items())))

    def run_used(self) -> None:
        for name, cls in USED_SCRAPERS.items():
            ok = True
            for group in ("ev", "ice"):
                if name not in self.cfg["used"]["searches"][group]["urls"]:
                    continue  # source switched off in config.yaml
                scraper = cls(self.cfg, self.ua, self.run_date, self.force, group=group, ai=self.ai)
                try:
                    listings = scraper.run()
                    stats = self.db.upsert_used_listings(listings, self.run_date)
                    log.info("%s %s: %d listings, %d new, %d drops", name, group, len(listings), stats["new"], stats["drops"])
                    vault.event("🔎", f"scraped {name} {group}", f"{len(listings)} listings, {stats['new']} new, {stats['drops']} drops")
                except Exception as exc:
                    ok = False
                    log.error("used scraper %s %s failed: %s", name, group, exc)
                    self.unavailable[f"used {name} {group}"] = str(exc)
                    vault.event("⚠️", f"{name} {group} unavailable", str(exc)[:160])
                finally:
                    scraper.close()
            if ok:
                vault.cars_gone(self.db, name, self.run_date)
                self.db.mark_gone(name, self.run_date)
        vault.track_cars(self.db.active_listings(), self.run_date)
        # The site ranks used cars by their brand's new registrations (LTA table M03).
        if self.cfg["used"].get("rank_by", "sales") == "sales" and not scrape_registrations(self.cfg, self.ua, self.run_date, self.force):
            self.unavailable["best selling used"] = "LTA registrations could not be read, used cars shown in value order"

    def run_new_ev(self) -> None:
        try:
            variants, notes = scrape_new_evs(self.cfg, self.ua, self.run_date, self.force)
            self.db.upsert_new_ev(variants, self.run_date)
            self.db.put_view360(view360_links(variants), self.run_date, exact=True)
            if notes:
                log.info("brand page check: %s", "; ".join(f"{k}: {v}" for k, v in sorted(notes.items())))
        except Exception as exc:
            log.error("new EV scrape failed: %s", exc)
            self.unavailable["new_ev"] = str(exc)
        # The site ranks new EVs by their brand's registrations per body type (LTA table M03 by body type).
        if (self.db.new_ev_on(self.run_date) and self.cfg["new_ev"].get("rank_by", "sales") == "sales"
                and not scrape_body_types(self.cfg, self.ua, self.run_date, self.force)):
            self.unavailable["best selling EV"] = "LTA registrations by body type could not be read, list shown in value order"

    def run_new_cars(self) -> None:
        """The full Sgcarmart new car list."""
        if not self.cfg.get("new_cars", {}).get("enabled"):
            return
        try:
            variants = scrape_new_cars(self.cfg, self.ua, self.run_date, self.force)
            self.db.upsert_new_cars(variants, self.run_date)
            # the listing says which models have a 360 view; the EV pages above gave the exact viewer for some
            self.db.put_view360({v.model_id: v.listing_url for v in variants if v.has_360 and v.model_id}, self.run_date, exact=False,
                                gone={v.model_id for v in variants if v.model_id and not v.has_360})
            log.info("new car listing: %d variants", len(variants))
            vault.event("🚗", "scraped new car list", f"{len(variants)} variants, {len({(v.make, v.model) for v in variants})} models")
        except Exception as exc:
            log.error("new car listing failed: %s", exc)
            self.unavailable["new_cars"] = str(exc)

    def run_official(self) -> None:
        """Prices from the brands' own websites."""
        if not self.cfg.get("official", {}).get("enabled"):
            return
        try:
            prices, status = scrape_official(self.cfg, self.ua, self.run_date, self.force)
            answered = {p.make for p in prices}
            self.db.upsert_official([p.as_row() for p in prices], self.run_date, answered)
            log.info("official prices: %d rows; %s", len(prices), "; ".join(f"{k} {v}" for k, v in sorted(status.items())))
            vault.event("🏷", "official prices", f"{len(prices)} rows from {len(answered)} brands")
        except Exception as exc:
            log.error("official prices failed: %s", exc)
            self.unavailable["official"] = str(exc)
