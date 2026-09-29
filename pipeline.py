"""Daily pipeline: run every scraper, persist results, build the report sections.

Each scraper is isolated. When one fails its section is marked unavailable and the run
continues, so a single site changing layout never blocks the rest of the report.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Optional

import costs
import report
from ai import ClaudeCli, analyst_note
from db import Database
from filters import shortlist, tagged
from models import CoeResult, CostBreakdown, Drivetrain, NewEvVariant, ReportSection, UsedListing
from scrapers.base import ScraperUnavailable
from scrapers.coe import next_tender_date, scrape_coe
from scrapers.fuel_cnergy import scrape_cnergy
from scrapers.fuel_price import pick_price, scrape_fuel_price
from scrapers.registrations import scrape_registrations
from scrapers.new_ev import group_by_body_type, rank_new_evs, scrape_new_evs
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
    "all": {"summary", "coe", "new_ev", "used_ev", "used_ice", "top_sellers", "fuel", "costs", "considerations"},
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
        self.new_ev_groups = None
        self.ai = ClaudeCli(cfg)

    # Steps

    def run_fuel(self) -> None:
        fp = scrape_fuel_price(self.cfg, self.ua, self.run_date, self.force)
        ice_cfg = self.cfg["costs"]["energy"]["ice"]
        station = ice_cfg.get("preferred_station")
        board = scrape_cnergy(self.cfg, self.ua, self.run_date, self.force) if station and station.lower() == "cnergy" else {}
        if board:
            if fp is None:
                from models import FuelPrice

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
                if name not in self.cfg["used"]["searches"][group]["urls"]:
                    continue  # source switched off in config.yaml
                scraper = cls(self.cfg, self.ua, self.run_date, self.force, group=group, ai=self.ai)
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
        stored = self.db.new_ev_on(self.run_date)
        self.new_evs = rank_new_evs(stored, self.cfg)
        self.new_ev_groups = group_by_body_type(stored, self.cfg) if self.cfg["new_ev"].get("group_by_body_type") else None

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
        # Link to the site the figures came from, not always LTA.
        src_key = {"onemotoring": "onemotoring_coe", "sgcarmart": "sgcarmart_coe_results", "motorist": "motorist_coe"}.get(latest.source, "onemotoring_coe")
        section = report.coe_section(latest.tender_date, latest.exercise, rows, nxt, self.cfg["sources"][src_key])
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
        self.cost_headers = []
        if self.new_evs:
            self.cost_headers.append("New EV")
            v = self.new_evs[0]
            picks.append((costs.for_new_ev(v, self.cfg, self.run_date), v.listing_url))
            self.financing.append(costs.financing_for_new(v, self.cfg))
        if self.used_ev:
            self.cost_headers.append("Used EV")
            l = self.used_ev[0][0]
            picks.append((costs.for_used(l, self.cfg, self.run_date, self.petrol_price), l.url))
            self.financing.append(costs.financing_for_used(l, self.cfg))
        if self.used_ice:
            self.cost_headers.append("Used ICE")
            l = self.used_ice[0][0]
            picks.append((costs.for_used(l, self.cfg, self.run_date, self.petrol_price), l.url))
            self.financing.append(costs.financing_for_used(l, self.cfg))
        return picks

    def build(self, section: str = "all") -> list[ReportSection]:
        wanted = SECTION_GROUPS[section]
        if wanted & {"costs", "used_ev", "used_ice", "fuel"}:
            self.run_fuel()
        if "coe" in wanted or "summary" in wanted:
            self.run_coe()
        if wanted & {"used_ev", "used_ice"}:
            self.run_used()
        if "new_ev" in wanted:
            self.run_new_ev()

        picks = self.cost_picks() if wanted & {"costs", "summary"} else []
        self.change_reasons: list[str] = []
        if "summary" in wanted and self.cfg["telegram"].get("send_only_on_change", False):
            raw = self.db.get_state("last_sent_signature")
            self.change_reasons = self.describe_changes(json.loads(raw) if raw else None, self.signature())
        sections: list[ReportSection] = []
        for key in self.cfg["telegram"]["section_order"]:
            if key not in wanted:
                continue
            if key == "summary":
                best = None
                if picks:
                    b, url = min(picks, key=lambda p: p[0].total_low)
                    best = (b.label, url, f"Lowest estimated annual cost, {fmt_money(b.total_low, '$')} to {fmt_money(b.total_high, '$')}.")
                summary = report.summary_section(
                    self.run_date, self.stats["new"], self.stats["drops"], self.stats["gone"], self.coe_line(), best,
                    unavailable=sorted(self.unavailable),
                )
                if self.change_reasons:
                    summary.html += "\n\nSince the last report: " + report.escape("; ".join(self.change_reasons)) + "."
                if self.cfg.get("ai", {}).get("analyst_note") and self.ai.available():
                    facts = {
                        "coe": {r.category.value: r.quota_premium for r in self.coe_latest},
                        "picks": [{"label": b.label, "annual_low": b.total_low, "annual_high": b.total_high} for b, _ in picks],
                        "changes": self.change_reasons,
                        "new": self.stats["new"], "drops": self.stats["drops"], "gone": self.stats["gone"],
                    }
                    note = analyst_note(self.ai, facts)
                    if note:
                        summary.html += "\n\n<i>" + report.escape(note) + "</i>"
                sections.append(summary)
            elif key == "coe":
                sections.append(self.coe_section())
            elif key == "new_ev":
                if self.new_evs:
                    s = report.new_ev_section(self.new_evs, self.cfg["telegram"]["table_width"], cfg=self.cfg, groups=self.new_ev_groups)
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
                    # Only this group's failures, after the fact that nothing passed, so a working
                    # source whose cars were all filtered out is not blamed on the broken ones.
                    group = "ev" if key == "used_ev" else "ice"
                    failed = [v for k, v in self.unavailable.items() if k.startswith("used") and k.endswith(" " + group)]
                    reason = "no listing passed the filters" + (". Failed sources: " + "; ".join(failed) if failed else "")
                    sections.append(report.unavailable_section(key, reason))
            elif key == "top_sellers":
                reg = scrape_registrations(self.cfg, self.ua, self.run_date, self.force)
                if reg:
                    sections.append(report.top_sellers_section(*reg, top_n=self.cfg.get("top_sellers", {}).get("top_n", 20),
                                                               max_width=self.cfg["telegram"]["table_width"],
                                                               source_url=self.cfg["sources"]["lta_registrations_by_make"]))
                else:
                    sections.append(report.unavailable_section("top_sellers", "LTA registrations table could not be read"))
            elif key == "fuel":
                fuel = self.db.latest_fuel_price()
                if fuel and (fuel.grades or fuel.station_prices):
                    sections.append(report.fuel_section(fuel, self.cfg["costs"]["energy"]["ice"].get("preferred_station")))
                else:
                    sections.append(report.unavailable_section("fuel", "pump price board could not be read"))
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
                        if fuel.station_prices:
                            station = self.cfg["costs"]["energy"]["ice"].get("preferred_station", "Station")
                            parts_ = []
                            for grade in ("92", "95", "98", "diesel"):
                                g = fuel.station_prices.get(grade)
                                if not g:
                                    continue
                                text = f"{grade} {g['public']:.2f}" if "public" in g else f"{grade} member {g.get('member', 0):.2f}"
                                if "public" in g and "member" in g:
                                    text += f" (member {g['member']:.2f})"
                                parts_.append(text)
                            assumptions += f" {station} today: " + ", ".join(parts_) + "."
                            member95 = fuel.station_prices.get("95", {}).get("member")
                            if member95:
                                for b, _ in picks:
                                    if b.drivetrain != Drivetrain.ev:
                                        alt = costs.energy_cost(b.drivetrain, self.cfg, member95)
                                        assumptions += f" {b.label} fuelled at {station} member price: {fmt_money(alt, '$')} a year instead of {fmt_money(b.energy, '$')}."
                    links = [(i["name"], i["url"]) for i in self.cfg["sources"]["insurance_comparison"]]
                    sections.append(report.costs_section(picks, assumptions, links, self.cfg["telegram"]["table_width"], financing=self.financing, headers=self.cost_headers))
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


    # Change detection

    def signature(self) -> dict[str, Any]:
        """Structured snapshot of the watched sections. Two runs with the same snapshot show the
        same cars at the same prices and the same COE tender, whatever the date."""
        sig: dict[str, Any] = {}
        watched = set(self.cfg["telegram"].get("change_sections", ["coe", "new_ev", "used_ev", "used_ice"]))
        if "coe" in watched:
            sig["coe"] = {r.category.value: r.quota_premium for r in self.coe_latest}
            sig["coe_tender"] = self.coe_latest[0].tender_date.isoformat() if self.coe_latest else None
        if "new_ev" in watched:
            items = [v for _, vs in (self.new_ev_groups or []) for v in vs] if self.new_ev_groups else self.new_evs
            sig["new_ev"] = sorted([v.make, v.model, v.variant, v.price_with_coe or 0] for v in items)
        if "used_ev" in watched:
            sig["used_ev"] = sorted([l.source, l.listing_id, l.price] for l, _ in self.used_ev)
        if "used_ice" in watched:
            sig["used_ice"] = sorted([l.source, l.listing_id, l.price] for l, _ in self.used_ice)
        return sig

    def describe_changes(self, previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
        """Human readable list of what differs between the last sent snapshot and this one."""
        if previous is None:
            return ["first report"]
        out = []
        if "coe" in current and previous.get("coe_tender") != current.get("coe_tender"):
            out.append(f"new COE tender {current.get('coe_tender')}")
        elif "coe" in current and previous.get("coe") != current.get("coe"):
            out.append("COE premiums corrected")
        if "new_ev" in current and previous.get("new_ev") != current.get("new_ev"):
            prev = {tuple(x[:3]): x[3] for x in previous.get("new_ev", [])}
            cur = {tuple(x[:3]): x[3] for x in current["new_ev"]}
            added = len(set(cur) - set(prev))
            removed = len(set(prev) - set(cur))
            repriced = sum(1 for k in cur if k in prev and prev[k] != cur[k])
            bits = [f"{n} {w}" for n, w in ((added, "added"), (removed, "removed"), (repriced, "repriced")) if n]
            out.append("new EV list: " + ", ".join(bits) if bits else "new EV list reordered")
        for key, label in (("used_ev", "used EV shortlist"), ("used_ice", "used petrol and hybrid shortlist")):
            if key in current and previous.get(key) != current.get(key):
                prev = {tuple(x[:2]): x[2] for x in previous.get(key, [])}
                cur = {tuple(x[:2]): x[2] for x in current[key]}
                added = len(set(cur) - set(prev))
                removed = len(set(prev) - set(cur))
                dropped = sum(1 for k in cur if k in prev and cur[k] < prev[k])
                bits = [f"{n} {w}" for n, w in ((added, "new"), (removed, "gone"), (dropped, "price drops")) if n]
                out.append(f"{label}: " + (", ".join(bits) if bits else "changed"))
        return out


def should_send(cfg: dict[str, Any], db: Database, pipe: "Pipeline", run_date: date, force: bool) -> tuple[bool, list[str], dict[str, Any]]:
    """Decide whether to send today. Returns (send, reasons, signature)."""
    sig = pipe.signature()
    if force or not cfg["telegram"].get("send_only_on_change", False):
        return True, ["sending regardless of changes"], sig
    raw = db.get_state("last_sent_signature")
    previous = json.loads(raw) if raw else None
    changes = pipe.describe_changes(previous, sig)
    if changes:
        return True, changes, sig
    heartbeat = int(cfg["telegram"].get("heartbeat_after_days", 0) or 0)
    last = db.last_sent_date()
    if heartbeat and (last is None or (run_date - last).days >= heartbeat):
        return True, [f"no changes, heartbeat after {heartbeat} quiet days"], sig
    return False, [], sig
