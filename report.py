"""Builds the seven report sections as Telegram ready HTML and renders them to the console.

Section builders take plain data so the scrapers, the cost engine and the sample report can
all feed them. Each builder returns a ReportSection whose html is a single Telegram message
(the sender splits it further only if it is over the limit).
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from rich.console import Console
from rich.panel import Panel

from models import CostBreakdown, NewEvVariant, ReportSection, UsedListing
from telegram_bot import (
    Column,
    build_section,
    escape,
    fmt_delta,
    fmt_int,
    fmt_money,
    link_list,
    pre_block,
    render_table,
    truncate,
)

SECTION_TITLES = {
    "summary": "SG car market daily",
    "coe": "COE tracker",
    "new_ev": "New EV price list",
    "used_ev": "Used EVs",
    "used_ice": "Used petrol and hybrid",
    "costs": "Cost of ownership, top 3",
    "considerations": "Buying considerations",
}


def unavailable_section(key: str, reason: str = "source unavailable today") -> ReportSection:
    title = SECTION_TITLES.get(key, key)
    body = f"This section is unavailable today. {escape(reason)}"
    return ReportSection(key=key, title=title, html=build_section(title, [body]), available=False)


# Section 1: summary


def summary_section(
    run_date: date,
    new_count: int,
    drop_count: int,
    gone_count: int,
    coe_line: str,
    best_pick: tuple[str, str, str] | None,
    unavailable: Sequence[str] = (),
) -> ReportSection:
    title = f"{SECTION_TITLES['summary']} {run_date.strftime('%a %d %b %Y')}"
    lines = [
        f"New listings: <b>{new_count}</b>",
        f"Price drops: <b>{drop_count}</b>",
        f"Gone since last run: <b>{gone_count}</b>",
        f"COE: {escape(coe_line)}",
    ]
    parts = ["\n".join(lines)]
    if best_pick:
        label, url, reason = best_pick
        parts.append(
            f"Best value today: <a href=\"{html_lib.escape(url, quote=True)}\">{escape(label)}</a>\n{escape(reason)}"
        )
    if unavailable:
        parts.append("Unavailable today: " + escape(", ".join(unavailable)))
    return ReportSection(key="summary", title=title, html=build_section(title, parts))


# Section 2: COE


def trend_arrows(premiums: Sequence[int]) -> str:
    """Arrows for consecutive changes, oldest to newest. Six tenders give five arrows."""
    arrows = []
    for older, newer in zip(premiums, premiums[1:]):
        arrows.append("▲" if newer > older else ("▼" if newer < older else "•"))
    return "".join(arrows) or "n/a"


def coe_section(
    tender_date: date,
    exercise: str,
    rows: Iterable[dict[str, Any]],
    next_tender: date | None,
    source_url: str | None = None,
) -> ReportSection:
    """rows: dicts with category, premium, delta, delta_pct, history (oldest first), bids, quota."""
    cols = [
        Column("Cat", 3),
        Column("Premium", 8, "right"),
        Column("Change", 15, "right"),
        Column("Trend", 6),
        Column("Bids/Quota", 11, "right"),
    ]
    table_rows = []
    for r in rows:
        bids = r.get("bids")
        quota = r.get("quota")
        bq = f"{fmt_int(bids)}/{fmt_int(quota)}" if bids is not None and quota is not None else "n/a"
        table_rows.append(
            [
                r["category"],
                fmt_money(r["premium"]),
                fmt_delta(r.get("delta"), r.get("delta_pct")),
                trend_arrows(r.get("history", [])),
                bq,
            ]
        )
    intro = f"Latest tender: {tender_date.strftime('%d %b %Y')}, {escape(exercise)}"
    footer = []
    if next_tender:
        footer.append(f"Next results expected: {next_tender.strftime('%a %d %b %Y')}")
    footer.append("Trend reads oldest to newest over the last six tenders.")
    if source_url:
        footer.append(f'Source: <a href="{html_lib.escape(source_url, quote=True)}">COE results</a>')
    return ReportSection(
        key="coe",
        title=SECTION_TITLES["coe"],
        html=build_section(SECTION_TITLES["coe"], [intro, pre_block(render_table(cols, table_rows)), "\n".join(footer)]),
    )


# Section 3: new EVs


def new_ev_section(variants: Sequence[NewEvVariant], max_width: int = 60) -> ReportSection:
    cols = [
        Column("#", 2, "right"),
        Column("Model", 21),
        Column("Price", 8, "right"),
        Column("Cat", 3),
        Column("Range", 5, "right"),
        Column("$/km", 5, "right"),
        Column("BatWty", 6, "right"),
    ]
    rows, links = [], []
    for n, v in enumerate(variants, start=1):
        rows.append(
            [
                n,
                v.display_name,
                fmt_money(v.price_with_coe),
                v.coe_category.value if v.coe_category else "?",
                fmt_int(v.range_km),
                f"{v.score:.0f}" if v.score else "n/a",
                f"{v.battery_warranty_years:g}y" if v.battery_warranty_years else "n/a",
            ]
        )
        links.append((f"{v.display_name} {fmt_money(v.price_with_coe, '$')}", v.listing_url))
    intro = "Price with COE. Score is price divided by claimed range, lower is better."
    return ReportSection(
        key="new_ev",
        title=SECTION_TITLES["new_ev"],
        html=build_section(SECTION_TITLES["new_ev"], [intro, pre_block(render_table(cols, rows, max_width)), link_list(links)]),
    )


# Section 4 and 5: used cars


def used_section(key: str, listings: Sequence[tuple[UsedListing, str]], max_width: int = 60) -> ReportSection:
    """listings: (listing, tag) pairs where tag is NEW, DROP ▼1,000 or empty."""
    cols = [
        Column("#", 2, "right"),
        Column("Car", 18),
        Column("Yr", 4, "right"),
        Column("km", 7, "right"),
        Column("Dep/yr", 7, "right"),
        Column("COE", 4, "right"),
        Column("Tag", 11),
    ]
    rows, links = [], []
    for n, (l, tag) in enumerate(listings, start=1):
        rows.append(
            [
                n,
                l.display_name,
                l.year or "n/a",
                fmt_int(l.mileage_km),
                fmt_money(l.depreciation_per_year),
                f"{l.coe_years_remaining:.1f}" if l.coe_years_remaining is not None else "n/a",
                tag,
            ]
        )
        detail = f"{l.display_name} {fmt_money(l.price, '$')}"
        if l.owners is not None:
            detail += f", {l.owners} owner" + ("s" if l.owners != 1 else "")
        if l.seller_type:
            detail += f", {l.seller_type}"
        links.append((detail, l.url))
    title = SECTION_TITLES[key]
    intro = "Ranked by lowest depreciation per year, then lowest mileage. COE is years left."
    return ReportSection(
        key=key,
        title=title,
        html=build_section(title, [intro, pre_block(render_table(cols, rows, max_width)), link_list(links)]),
    )


# Section 6: cost of ownership


def costs_section(picks: Sequence[tuple[CostBreakdown, str]], assumptions: str, insurance_links: Sequence[tuple[str, str]] = (), max_width: int = 60) -> ReportSection:
    """picks: (breakdown, url) for best new EV, best used EV, best used ICE or hybrid, in that order."""
    headers = ["New EV", "Used EV", "Used ICE"][: len(picks)]
    cols = [Column("Per year", 14)] + [Column(h, 14, "right") for h in headers]

    def row(label: str, getter) -> list:
        return [label] + [getter(b) for b, _ in picks]

    rows = [
        row("Road tax", lambda b: fmt_money(b.road_tax)),
        row("Insurance low", lambda b: fmt_money(b.insurance_low)),
        row("Insurance high", lambda b: fmt_money(b.insurance_high)),
        row("Depreciation", lambda b: fmt_money(b.depreciation)),
        row("Energy", lambda b: fmt_money(b.energy)),
        row("Fixed extras", lambda b: fmt_money(b.fixed_extras)),
        row("Total low", lambda b: fmt_money(b.total_low)),
        row("Total high", lambda b: fmt_money(b.total_high)),
    ]
    links = [(f"{h}: {b.label}", url) for h, (b, url) in zip(headers, picks)]
    parts = [pre_block(render_table(cols, rows, max_width)), link_list(links), escape(assumptions)]
    if insurance_links:
        parts.append("Get a real insurance quote: " + ", ".join(
            f'<a href="{html_lib.escape(u, quote=True)}">{escape(n)}</a>' for n, u in insurance_links
        ))
    return ReportSection(key="costs", title=SECTION_TITLES["costs"], html=build_section(SECTION_TITLES["costs"], parts))


# Section 7: buying considerations


def considerations_section(cfg: dict[str, Any], cat_a: int | None, cat_b: int | None, sources: Sequence[tuple[str, str]] = ()) -> ReportSection:
    bc = cfg["buying_considerations"]
    parts = []
    parts.append("<b>Loan rules</b>\n" + escape(bc["loan_rules"].strip()))
    parts.append("<b>ARF and PARF</b>\n" + escape(bc["arf_parf"].strip()))
    inc_lines = []
    for inc in bc.get("ev_incentives", []):
        ends = f" Ends {inc['ends']}." if inc.get("ends") else ""
        inc_lines.append(f"• {escape(inc['name'])}: {escape(inc['detail'].strip())}{ends}")
    parts.append("<b>EV incentives</b>\n" + "\n".join(inc_lines))
    rule = bc["rule_of_thumb"].strip().format(cat_a_threshold=fmt_money(bc["cat_a_threshold_sgd"], "$"))
    if cat_a is not None and cat_b is not None:
        verdict = (
            "Today Cat A is above the threshold, which favours a well kept used car."
            if cat_a > bc["cat_a_threshold_sgd"]
            else "Today Cat A is below the threshold, which keeps new cars competitive."
        )
        rule += f"\nCat A {fmt_money(cat_a, '$')}, Cat B {fmt_money(cat_b, '$')}. {verdict}"
    parts.append("<b>New versus used today</b>\n" + escape(rule))
    if bc.get("policy_watch"):
        parts.append("<b>Policy watch</b>\n" + "\n".join("• " + escape(p) for p in bc["policy_watch"]))
    if sources:
        parts.append("<b>Sources</b>\n" + "\n".join(
            f'• <a href="{html_lib.escape(u, quote=True)}">{escape(n)}</a>' for n, u in sources
        ))
    verified = bc.get("verified_on") or "not yet verified"
    parts.append(f"Assumptions last verified: {escape(verified)}. Edit them in config.yaml.")
    return ReportSection(key="considerations", title=SECTION_TITLES["considerations"], html=build_section(SECTION_TITLES["considerations"], parts))


# Console rendering for dry runs

_TAG_RE = re.compile(r"<a href=\"([^\"]+)\">(.*?)</a>|<[^>]+>")


def html_to_text(text: str) -> str:
    def repl(m: re.Match) -> str:
        if m.group(1):
            return f"{m.group(2)} <{m.group(1)}>"
        return ""

    return html_lib.unescape(_TAG_RE.sub(repl, text))


def render_console(sections: Sequence[ReportSection], console: Console | None = None) -> None:
    console = console or Console()
    for s in sections:
        style = "green" if s.available else "yellow"
        console.print(Panel(html_to_text(s.html), title=f"{s.key} ({len(s.html)} chars)", border_style=style, expand=False))


# Hardcoded sample used to confirm Telegram delivery before any scraper exists


def sample_report(cfg: dict[str, Any], run_date: date | None = None) -> list[ReportSection]:
    run_date = run_date or date.today()
    tender = run_date - timedelta(days=(run_date.weekday() - 2) % 7)
    coe_rows = [
        {"category": "A", "premium": 104000, "delta": 2500, "delta_pct": 2.5, "history": [96000, 98500, 101000, 99000, 101500, 104000], "bids": 1620, "quota": 1290},
        {"category": "B", "premium": 121500, "delta": -1500, "delta_pct": -1.2, "history": [118000, 119000, 122500, 124000, 123000, 121500], "bids": 1310, "quota": 1010},
        {"category": "C", "premium": 71000, "delta": 0, "delta_pct": 0.0, "history": [68000, 69500, 70000, 71000, 71000, 71000], "bids": 380, "quota": 310},
        {"category": "E", "premium": 122000, "delta": 1000, "delta_pct": 0.8, "history": [117000, 119500, 120000, 122500, 121000, 122000], "bids": 240, "quota": 90},
    ]
    new_evs = [
        NewEvVariant(make="BYD", model="Atto 3", variant="Dynamic", price_with_coe=169888, coe_category="A", range_km=420, power_kw=150, battery_warranty_years=8, battery_kwh=60.5, listing_url="https://www.sgcarmart.com/new_cars/", price_source_url="https://www.byd.com/sg", source="sample"),
        NewEvVariant(make="MG", model="4", variant="Standard", price_with_coe=158888, coe_category="A", range_km=350, power_kw=125, battery_warranty_years=7, listing_url="https://www.sgcarmart.com/new_cars/", price_source_url="https://www.mg.com.sg", source="sample"),
        NewEvVariant(make="Tesla", model="Model 3", variant="RWD", price_with_coe=198000, coe_category="B", range_km=513, power_kw=208, battery_warranty_years=8, listing_url="https://www.tesla.com/en_sg/model3", price_source_url="https://www.tesla.com/en_sg", source="sample"),
        NewEvVariant(make="Xpeng", model="G6", variant="Standard Range", price_with_coe=188999, coe_category="B", range_km=435, power_kw=190, battery_warranty_years=8, listing_url="https://www.sgcarmart.com/new_cars/", price_source_url="https://www.xpeng.com/sg", source="sample"),
        NewEvVariant(make="BYD", model="Seal", variant="Premium", price_with_coe=209888, coe_category="B", range_km=570, power_kw=230, battery_warranty_years=8, listing_url="https://www.sgcarmart.com/new_cars/", price_source_url="https://www.byd.com/sg", source="sample"),
    ]
    new_evs.sort(key=lambda v: (v.score or 9e9, -(v.battery_warranty_years or 0)))

    def used(**kw) -> UsedListing:
        base = dict(source="sample", listing_id=kw["listing_id"], url=f"https://www.sgcarmart.com/used_cars/info.php?ID={kw['listing_id']}", variant="", seller_type="dealer")
        base.update(kw)
        return UsedListing(**base)

    used_ev = [
        (used(listing_id="1401", make="BYD", model="Atto 3", drivetrain="ev", year=2023, mileage_km=28000, owners=1, price=118800, depreciation_per_year=11900, coe_years_remaining=7.4, power_kw=150), "NEW"),
        (used(listing_id="1402", make="Tesla", model="Model 3", variant="SR+", drivetrain="ev", year=2021, mileage_km=61000, owners=2, price=109800, depreciation_per_year=13400, coe_years_remaining=5.9, power_kw=208), "DROP ▼2,000"),
        (used(listing_id="1403", make="Hyundai", model="Ioniq 5", drivetrain="ev", year=2022, mileage_km=44000, owners=1, price=119500, depreciation_per_year=13900, coe_years_remaining=6.6, power_kw=160), ""),
    ]
    used_ice = [
        (used(listing_id="2201", make="Toyota", model="Corolla Altis", variant="1.6 Hybrid", drivetrain="hybrid", year=2021, mileage_km=52000, owners=1, price=98800, depreciation_per_year=11200, coe_years_remaining=6.2, engine_cc=1598), ""),
        (used(listing_id="2202", make="Honda", model="Civic", variant="1.5 VTEC Turbo", drivetrain="ice", year=2022, mileage_km=38000, owners=1, price=112800, depreciation_per_year=12600, coe_years_remaining=7.1, engine_cc=1498), "NEW"),
        (used(listing_id="2203", make="Mazda", model="3", variant="1.5 Mild Hybrid", drivetrain="hybrid", year=2020, mileage_km=63000, owners=2, price=88800, depreciation_per_year=12900, coe_years_remaining=4.9, engine_cc=1496), "DROP ▼1,500"),
    ]
    costs = [
        (CostBreakdown(label="MG 4 Standard (new)", drivetrain="ev", road_tax=1258, insurance_low=2300, insurance_high=3600, depreciation=13600, energy=936, fixed_extras=2900), new_evs[0].listing_url),
        (CostBreakdown(label="BYD Atto 3 2023 (used)", drivetrain="ev", road_tax=1404, insurance_low=1800, insurance_high=2800, depreciation=11900, energy=936, fixed_extras=2900), used_ev[0][0].url),
        (CostBreakdown(label="Toyota Altis Hybrid 2021", drivetrain="hybrid", road_tax=742, insurance_low=1400, insurance_high=2300, depreciation=11200, energy=2351, fixed_extras=3300), used_ice[0][0].url),
    ]
    insurance_links = [(i["name"], i["url"]) for i in cfg["sources"]["insurance_comparison"]]
    sources = [
        ("LTA OneMotoring COE results", cfg["sources"]["onemotoring_coe"]),
        ("Sgcarmart used cars", cfg["sources"]["sgcarmart_used_listing"]),
    ]
    sections = [
        summary_section(
            run_date, new_count=2, drop_count=2, gone_count=1,
            coe_line="Cat A $104,000 ▲2,500, Cat B $121,500 ▼1,500",
            best_pick=("BYD Atto 3 2023, 28,000 km, $118,800", used_ev[0][0].url, "Lowest depreciation per year in the used EV list with 7.4 years of COE left."),
        ),
        coe_section(tender, "sample tender", coe_rows, tender + timedelta(days=14), cfg["sources"]["onemotoring_coe"]),
        new_ev_section(new_evs),
        used_section("used_ev", used_ev),
        used_section("used_ice", used_ice),
        costs_section(costs, cfg["costs"]["insurance"]["assumptions"].strip(), insurance_links),
        considerations_section(cfg, 104000, 121500, sources),
    ]
    for s in sections:
        s.html = "<i>SAMPLE DATA, delivery test</i>\n" + s.html
    return sections
