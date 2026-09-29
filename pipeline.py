"""Daily pipeline: run every scraper, persist results, build the report sections.

Each scraper is isolated. When one fails its section is marked unavailable and the run
continues, so a single site changing layout never blocks the rest of the report.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

import costs
import report
from db import Database
from filters import shortlist, tagged
from models import CoeResult, CostBreakdown, Drivetrain, NewEvVariant, ReportSection, UsedListing
from scrapers.base import ScraperUnavailable
from scrapers.coe import next_tender_date, scrape_coe
from scrapers.fuel_price import scrape_fuel_price
from scrapers.new_ev import rank_new_evs, scrape_new_evs
from scrapers.used_carro import CarroUsedScraper
from scrapers.used_motorist import MotoristUsedScraper
from scrapers.used_sgcarmart import SgcarmartUsedScraper
from telegram_bot import fmt_delta, fmt_money

log = logging.getLogger(__name__)

USED_SCRAPERS = {
    "sgcarmart": SgcarmartUsedScraper,
    "carro": CarroUsedScraper,
    "motorist": MotoristUsedScraper,
}

SECTION_GROUPS = {
    "coe": {"coe"},
    "new": {"new_ev"},
    "used": {"used_ev", "used_ice"},
    "costs": {"costs", "new_ev", "used_ev", "used_ice", "coe"},
    "all": {"summary", "coe", "new_ev", "used_ev", "used_ice", "costs", "considerations"},
}


class Pipeline:
    def __init__(self, cfg: dict[str, Any], db: Database, run_date: date, user_agent: str, force: bool = False, since: date | None = None):
        self.cfg = cfg
        self.db = db
        self.run_date = run_date
        self.since = since or run_date
        self.ua = user_agent
        self.force = force
        self.unavailable: dict[str, str] = {}
        self.stats = {"new": 0, "drops": 0, "gone": 0}
        self.petrol_price: Optional[float] = None
        self.coe_latest: list[CoeResult] = []
        self.new_evs: list[NewEvVariant] = []
        self.brand_notes: dict[str, str] = {}
        self.used_ev: list[tuple[UsedListing, str]] = []
        self.used_ice: list[tuple[UsedListing, str]] = []
        self.rejections: dict[str, int] = {}
        self.financing: list = []

    # Steps

    def run_fuel(self) -> None:
        fp = scrape_fuel_price(self.cfg, self.ua, self.run_date, self.force)
        if fp:
            self.db.upsert_fuel_price(fp)
        latest = self.db.latest_fuel_price()
        self.petrol_price = latest.ron95_per_litre if latest else None

    def run_coe(self) -> None:
        try:
            results = scrape_coe(self.cfg, self.ua, self.run_date, self.force)
            self.db.upsert_coe_results(results)
        except Exception as exc:
            log.error("COE scrape failed: %s", exc)
            self.unavailable["coe"] = str(exc)
        self.coe_latest = self.db.latest_coe()

    def run_used(self) -> None:
        for name, cls in USED_SCRAPERS.items():
            ok = True
            for group in ("ev", "ice"):
                scraper = cls(self.cfg, self.ua, self.run_date, self.force, group=group)
                try:
                    listings = scraper.run()
                    stats = self.db.upsert_used_listings(listings, self.run_date)
                    self.stats["new"] += stats["new"]
                    self.stats["drops"] += stats["drops"]
                    log.info("%s %s: %d listings, %d new, %d drops", name, group, len(listings), stats["new"], stats["drops"])
                except Exception as exc:
                    ok = False
                    log.error("used scraper %s %s failed: %s", name, group, exc)
                    self.unavailable[f"used {name} {group}"] = str(exc)
                finally:
                    scraper.close()
            if ok:
                self.stats["gone"] += self.db.mark_gone(name, self.run_date)
        if self.since != self.run_date:
            self.stats["gone"] = self.db.count_gone_since(self.since)
        listings = self.db.active_listings()
        for l in listings:
            if l.depreciation_per_year is None:
                l.depreciation_per_year = costs.depreciation_used(l, self.cfg, self.run_date)
        evs = [l for l in listings if l.drivetrain == Drivetrain.ev]
        others = [l for l in listings if l.drivetrain != Drivetrain.ev]
        top_ev, rej_ev = shortlist(evs, self.cfg, self.run_date)
        top_ice, rej_ice = shortlist(others, self.cfg, self.run_date)
        self.rejections = dict(rej_ev + rej_ice)
        self.used_ev = tagged(top_ev, self.since)
        self.used_ice = tagged(top_ice, self.since)
        if self.since != self.run_date:
            for l in listings:
                if l.first_seen and l.first_seen >= self.since:
                    self.stats["new"] += 0  # counted below
            self.stats["new"] = sum(1 for l in listings if l.first_seen and l.first_seen >= self.since)
            self.stats["drops"] = sum(1 for l in listings if any(p.seen_on < self.since and p.price > l.price for p in l.price_history))

    def run_new_ev(self) -> None:
        try:
            variants, notes = scrape_new_evs(self.cfg, self.ua, self.run_date, self.force)
            self.db.upsert_new_ev(variants, self.run_date)
            self.brand_notes = notes
        except Exception as exc:
            log.error("new EV scrape failed: %s", exc)
            self.unavailable["new_ev"] = str(exc)
        self.new_evs = rank_new_evs(self.db.new_ev_on(self.run_date), self.cfg)

    # Sections

    def coe_section(self) -> ReportSection:
        if not self.coe_latest:
            return report.unavailable_section("coe", self.unavailable.get("coe", "no tender stored yet"))
        rows = []
        window = self.cfg["coe"]["trend_window"]
        for r in self.coe_latest:
            if r.category.value not in self.cfg["coe"]["display_categories"]:
                continue
            history = list(reversed(self.db.coe_history(r.category.value, window)))
            delta = pct = None
            if len(history) >= 2:
                prev = history[-2].quota_premium
                delta = r.quota_premium - prev
                pct = delta / prev * 100 if prev else None
            rows.append({
                "category": r.category.value, "premium": r.quota_premium, "delta": delta, "delta_pct": pct,
                "history": [h.quota_premium for h in history], "bids": r.bids_received, "quota": r.quota,
            })
        latest = self.coe_latest[0]
        weeks = tuple(self.cfg["coe"]["tender_weeks_of_month"])
        nxt = next_tender_date(max(self.run_date, latest.tender_date), weeks, self.cfg["coe"]["results_weekday"])
        section = report.coe_section(latest.tender_date, latest.exercise, rows, nxt, self.cfg["sources"]["onemotoring_coe"])
        if "coe" in self.unavailable:
            section.html += "\n\nLive fetch failed today, showing the last stored tender."
        return section

    def coe_line(self) -> str:
        parts = []
        for r in self.coe_latest:
            if r.category.value in ("A", "B"):
                hist = self.db.coe_history(r.category.value, 2)
                delta = r.quota_premium - hist[1].quota_premium if len(hist) == 2 else None
                parts.append(f"Cat {r.category.value} {fmt_money(r.quota_premium, '$')}" + (f" {fmt_delta(delta)}" if delta is not None else ""))
        return ", ".join(parts) if parts else "no COE data"

    def premium(self, cat: str) -> Optional[int]:
        return next((r.quota_premium for r in self.coe_latest if r.category.value == cat), None)

    def cost_picks(self) -> list[tuple[CostBreakdown, str]]:
        picks = []
        self.financing = []
        if self.new_evs:
            v = self.new_evs[0]
            picks.append((costs.for_new_ev(v, self.cfg, self.run_date), v.listing_url))
            self.financing.append(costs.financing_for_new(v, self.cfg))
        if self.used_ev:
            l = self.used_ev[0][0]
            picks.append((costs.for_used(l, self.cfg, self.run_date, self.petrol_price), l.url))
            self.financing.append(costs.financing_for_used(l, self.cfg))
        if self.used_ice:
            l = self.used_ice[0][0]
            picks.append((costs.for_used(l, self.cfg, self.run_date, self.petrol_price), l.url))
            self.financing.append(costs.financing_for_used(l, self.cfg))
        return picks

    def build(self, section: str = "all") -> list[ReportSection]:
        wanted = SECTION_GROUPS[section]
        if wanted & {"costs", "used_ev", "used_ice"}:
            self.run_fuel()
        if "coe" in wanted or "summary" in wanted:
            self.run_coe()
        if wanted & {"used_ev", "used_ice"}:
            self.run_used()
        if "new_ev" in wanted:
            self.run_new_ev()

        picks = self.cost_picks() if wanted & {"costs", "summary"} else []
        sections: list[ReportSection] = []
        for key in self.cfg["telegram"]["section_order"]:
            if key not in wanted:
                continue
            if key == "summary":
                best = None
                if picks:
                    b, url = min(picks, key=lambda p: p[0].total_low)
                    best = (b.label, url, f"Lowest estimated annual cost, {fmt_money(b.total_low, '$')} to {fmt_money(b.total_high, '$')}.")
                sections.append(report.summary_section(
                    self.run_date, self.stats["new"], self.stats["drops"], self.stats["gone"], self.coe_line(), best,
                    unavailable=sorted(self.unavailable),
                ))
            elif key == "coe":
                sections.append(self.coe_section())
            elif key == "new_ev":
                if self.new_evs:
                    s = report.new_ev_section(self.new_evs, self.cfg["telegram"]["table_width"], cfg=self.cfg)
                    if self.brand_notes:
                        s.html += "\n\nBrand page check: " + "; ".join(f"{k}: {v}" for k, v in sorted(self.brand_notes.items()))
                    sections.append(s)
                else:
                    sections.append(report.unavailable_section("new_ev", self.unavailable.get("new_ev", "no variants parsed")))
            elif key in ("used_ev", "used_ice"):
                rows = self.used_ev if key == "used_ev" else self.used_ice
                if rows:
                    sections.append(report.used_section(key, rows, self.cfg["telegram"]["table_width"], cfg=self.cfg))
                else:
                    reason = "; ".join(v for k, v in self.unavailable.items() if k.startswith("used")) or "no listing passed the filters"
                    sections.append(report.unavailable_section(key, reason))
            elif key == "costs":
                if picks:
                    assumptions = self.cfg["costs"]["insurance"]["assumptions"].strip()
                    fuel = self.db.latest_fuel_price()
                    if fuel:
                        brands = ", ".join(f"{b} {p:.2f}" for b, p in sorted(fuel.by_brand.items(), key=lambda t: t[1]))
                        assumptions += (f" Petrol 95 at {fuel.ron95_per_litre:.2f} per litre ({self.cfg['costs']['energy']['ice'].get('price_pick', 'median')} of listed pump prices"
                                        f" before card or loyalty discounts, {fuel.observed_on.isoformat()}).")
                        if brands:
                            assumptions += f" By brand: {brands}."
                    links = [(i["name"], i["url"]) for i in self.cfg["sources"]["insurance_comparison"]]
                    sections.append(report.costs_section(picks, assumptions, links, self.cfg["telegram"]["table_width"], financing=self.financing))
                else:
                    sections.append(report.unavailable_section("costs", "no shortlisted cars to compare"))
            elif key == "considerations":
                src = self.cfg["sources"]
                links = [
                    ("LTA COE results", src["onemotoring_coe"]),
                    ("LTA tax structure", src["lta_tax_structure"]),
                    ("LTA PARF and COE rebate", src["lta_parf"]),
                    ("MAS motor vehicle loan rules", src["mas_loan_rules"]),
                    ("LTA VES and EEAI extension", src["lta_ves_eeai_extension"]),
                ]
                sections.append(report.considerations_section(self.cfg, self.premium("A"), self.premium("B"), links))
        return sections
