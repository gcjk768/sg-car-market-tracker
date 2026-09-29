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

from models import CostBreakdown, Financing, NewEvVariant, ReportSection, UsedListing
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
    "new_ev": "New EV Car Best Value list",
    "used_ev": "Used EV Car Best Value list",
    "used_ice": "Used Petrol Car Best Value list",
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


def _finance_note(fin: Financing | None) -> str:
    if fin is None:
        return ""
    return f", deposit {fmt_money(fin.deposit, '$')}, {fmt_money(fin.monthly, '$')}/mth over {fin.tenure_years}y"


def new_ev_section(variants: Sequence[NewEvVariant], max_width: int = 60, cfg: dict[str, Any] | None = None,
                   groups: Sequence[tuple[str, Sequence[NewEvVariant]]] | None = None) -> ReportSection:
    """One table for the whole list, or one table per body type when `groups` is given.
    Row numbers run on across the groups so they match the link list."""
    cols = [
        Column("#", 2, "right"),
        Column("Car", 18),
        Column("Price", 7, "right"),
        Column("$/km", 4, "right"),
        Column("Deposit", 7, "right"),
        Column("Mth 7y", 6, "right"),
        Column("Dep/yr", 7, "right"),
    ]
    tables: list[tuple[str, list]] = []
    links = []
    ordered = list(groups) if groups else [("", list(variants))]
    titles = (cfg or {}).get("new_ev", {}).get("body_type_titles", {})
    n = 0
    for title, items in ordered:
        rows = []
        for v in items:
            n += 1
            rows.append(_new_ev_row(n, v, cfg))
            links.append(_new_ev_link(v, cfg))
        tables.append((titles.get(title, title), rows))
    intro = "Value score is price with COE divided by claimed range, lower is better."
    all_items = [v for _, items in ordered for v in items]
    if all_items and all(v.price_includes_rebates for v in all_items):
        intro += " Prices are dealer figures net of the VES and EEAI rebates."
    elif all_items:
        intro += " Prices are before rebates where marked."
    if cfg:
        f = cfg["costs"]["financing"]
        intro += (f" Deposit is the minimum under the MAS rule, instalment on the rest at {f['flat_rate_new'] * 100:.2f} percent"
                  f" flat over {f['max_tenure_years']} years. Depreciation is indicative over"
                  f" {cfg['costs']['depreciation']['new_car_horizon_years']} years and does not affect the order.")
    parts = [intro]
    for title, rows in tables:
        block = render_table(cols, rows, max_width)
        if title:
            block = f"{title}\n{block}"
        parts.append(pre_block(block))
    parts.append(link_list(links))
    return ReportSection(key="new_ev", title=SECTION_TITLES["new_ev"], html=build_section(SECTION_TITLES["new_ev"], parts))


def _new_ev_row(n: int, v: NewEvVariant, cfg: dict[str, Any] | None = None) -> list:
    fin = _fin(cfg, v.price_with_coe, None, True)
    dep = None
    if cfg and v.price_with_coe:
        from costs import depreciation_new

        dep = depreciation_new(v.price_with_coe, None, cfg, date.today())
    return [
        n,
        v.display_name,
        fmt_money(v.price_with_coe),
        f"{v.score:.0f}" if v.score else "n/a",
        fmt_money(fin.deposit) if fin else "n/a",
        fmt_money(fin.monthly) if fin else "n/a",
        f"-{fmt_int(dep)}" if dep else "n/a",
    ]


def _new_ev_link(v: NewEvVariant, cfg: dict[str, Any] | None) -> tuple[str, str]:
    # The table already carries price and finance, so the link line adds range and category.
    bits = [f"{fmt_int(v.range_km)} km" if v.range_km else None,
            f"Cat {v.coe_category.value}" if v.coe_category else None,
            f"{v.battery_warranty_years:g}y battery warranty" if v.battery_warranty_years else None]
    return (f"{v.display_name}, " + ", ".join(b for b in bits if b), v.listing_url)


# Section 4 and 5: used cars


def _fin(cfg: dict[str, Any] | None, price: int | None, omv: int | None, new_car: bool) -> Financing | None:
    if not cfg or not price:
        return None
    from costs import financing

    return financing(price, omv, cfg, new_car)


def _finance_intro(cfg: dict[str, Any], new_car: bool) -> str:
    f = cfg["costs"]["financing"]
    rate = f["flat_rate_new"] if new_car else f["flat_rate_used"]
    return (f"Deposit is the minimum under the MAS loan rules, instalment at {rate * 100:.2f}% flat "
            f"over {f['max_tenure_years']} years.")


def used_section(key: str, listings: Sequence[tuple[UsedListing, str]], max_width: int = 60, cfg: dict[str, Any] | None = None) -> ReportSection:
    """listings: (listing, tag) pairs where tag is NEW, DROP ▼1,000 or empty."""
    cols = [
        Column("#", 2, "right"),
        Column("Car", 14),
        Column("Price", 7, "right"),
        Column("Deposit", 7, "right"),
        Column("Mth 7y", 6, "right"),
        Column("Dep/yr", 6, "right"),
        Column("COE", 3, "right"),
        Column("km", 6, "right"),
    ]
    rows, links = [], []
    for n, (l, tag) in enumerate(listings, start=1):
        fin = _fin(cfg, l.price, l.omv, False)
        rows.append(
            [
                n,
                l.display_name,
                fmt_money(l.price),
                fmt_money(fin.deposit) if fin else "n/a",
                fmt_money(fin.monthly) if fin else "n/a",
                fmt_money(l.depreciation_per_year),
                f"{l.coe_years_remaining:.1f}" if l.coe_years_remaining is not None else "n/a",
                fmt_int(l.mileage_km),
            ]
        )
        bits = [str(l.year) if l.year else None,
                f"{l.owners} owner" + ("s" if l.owners != 1 else "") if l.owners is not None else None,
                l.seller_type, tag or None]
        links.append((f"{l.display_name}, " + ", ".join(b for b in bits if b), l.url))
    title = SECTION_TITLES[key]
    intro = "Ranked by lowest depreciation per year, then lowest mileage. COE is years left."
    if cfg:
        f = cfg["costs"]["financing"]
        intro += (f" Deposit is the minimum under the MAS rule, instalment on the rest at"
                  f" {f['flat_rate_used'] * 100:.2f} percent flat over {f['max_tenure_years']} years.")
    return ReportSection(
        key=key,
        title=title,
        html=build_section(title, [intro, pre_block(render_table(cols, rows, max_width)), link_list(links)]),
    )


# Section 6: cost of ownership


def costs_section(picks: Sequence[tuple[CostBreakdown, str]], assumptions: str, insurance_links: Sequence[tuple[str, str]] = (), max_width: int = 60, financing: Sequence[Financing | None] = (), headers: Sequence[str] = ()) -> ReportSection:
    """picks: (breakdown, url) for best new EV, best used EV, best used ICE or hybrid, in that order.
    financing: one Financing per pick, in the same order, or empty to leave the rows out."""
    # Name each column after its pick; a missing pick must not shift the others' names.
    headers = list(headers) or ["New EV", "Used EV", "Used ICE"][: len(picks)]
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
    fins = list(financing)
    if fins and any(fins):
        def frow(label: str, getter) -> list:
            return [label] + [getter(f) if f else "n/a" for f in fins]

        rows += [
            frow("Deposit", lambda f: f"{fmt_int(f.deposit)} ({f.ltv * 100:.0f}% LTV)".replace("(60% LTV)", "(40%)").replace("(70% LTV)", "(30%)")),
            frow("Loan", lambda f: fmt_money(f.loan)),
            frow(f"Mth {fins[0].tenure_years if fins[0] else 7}y loan", lambda f: fmt_money(f.monthly)),
            frow(f"Mth {fins[0].short_tenure_years if fins[0] else 5}y loan", lambda f: fmt_money(f.monthly_short)),
        ]
    links = [(f"{h}: {b.label}", url) for h, (b, url) in zip(headers, picks)]
    parts = [pre_block(render_table(cols, rows, max_width)), link_list(links), escape(assumptions)]
    if fins and any(fins):
        rates = sorted({f.rate_flat for f in fins if f})
        parts.append(escape("Deposit is the minimum under the MAS rules: 40% of price when OMV is above 20,000, 30% otherwise. "
                            "Instalments use a flat rate of " + " and ".join(f"{r * 100:.2f}%" for r in rates) + " per year. "
                            "The 7 year loan costs the most interest; the 5 year figure shows the trade off."))
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
    # Real figures from the 23 September 2026 tender. Earlier history points are illustrative.
    coe_rows = [
        {"category": "A", "premium": 131890, "delta": -1119, "delta_pct": -0.84, "history": [122000, 125500, 128501, 130000, 133009, 131890], "bids": 1507, "quota": 1193},
        {"category": "B", "premium": 133000, "delta": -2000, "delta_pct": -1.48, "history": [124000, 127500, 130000, 132000, 135000, 133000], "bids": 1135, "quota": 927},
        {"category": "C", "premium": 92144, "delta": -956, "delta_pct": -1.03, "history": [80000, 84000, 88000, 90500, 93100, 92144], "bids": 532, "quota": 318},
        {"category": "E", "premium": 137000, "delta": -900, "delta_pct": -0.65, "history": [126000, 129000, 133000, 136000, 137900, 137000], "bids": 431, "quota": 269},
    ]
    # Figures researched on 2026-09-29 from dealer and press pages, prices with COE.
    src = "https://www.sgcarmart.com/new_cars/"
    new_evs = [
        NewEvVariant(make="Tesla", model="Model 3", variant="RWD 110", price_with_coe=179999, coe_category="A", range_km=534, range_standard="WLTP", power_kw=110, battery_kwh=62.5, battery_warranty_years=8, listing_url="https://www.tesla.com/en_sg/model3", price_source_url="https://www.tesla.com/en_sg", source="sample"),
        NewEvVariant(make="GAC Aion", model="UT", variant="Premium", price_with_coe=148988, coe_category="A", range_km=410, range_standard="WLTP", power_kw=100, battery_kwh=60, battery_warranty_years=8, listing_url=src, price_source_url="https://www.aion.sg", source="sample"),
        NewEvVariant(make="GAC Aion", model="Y Plus", variant="Premium", price_with_coe=159988, coe_category="A", range_km=430, power_kw=100, battery_kwh=63.2, battery_warranty_years=8, listing_url=src, price_source_url="https://www.aion.sg", source="sample"),
        NewEvVariant(make="BYD", model="Sealion 7", variant="Dynamic", price_with_coe=205388, coe_category="A", range_km=540, range_standard="WLTP", power_kw=110, battery_kwh=82.5, battery_warranty_years=8, listing_url=src, price_source_url="https://www.byd.com/sg", source="sample"),
        NewEvVariant(make="MG", model="MGS5 EV", variant="Luxury promo", price_with_coe=165888, coe_category="A", range_km=425, range_standard="WLTP", power_kw=99, battery_kwh=62, battery_warranty_years=7, listing_url=src, price_source_url="https://www.mg.com.sg", source="sample"),
        NewEvVariant(make="BYD", model="Atto 3", variant="Dynamic", price_with_coe=171888, coe_category="B", range_km=420, range_standard="WLTP", power_kw=150, battery_kwh=60.5, battery_warranty_years=8, listing_url=src, price_source_url="https://www.byd.com/sg", source="sample"),
        NewEvVariant(make="MG", model="4", variant="Urban", price_with_coe=168888, coe_category="A", range_km=405, range_standard="WLTP", power_kw=110, battery_kwh=62, battery_warranty_years=7, listing_url=src, price_source_url="https://www.mg.com.sg", source="sample"),
        NewEvVariant(make="BYD", model="Seal 6 EV", variant="Premium", price_with_coe=179888, coe_category="A", range_km=425, range_standard="WLTC", power_kw=95, battery_warranty_years=8, listing_url=src, price_source_url="https://www.byd.com/sg", source="sample"),
        NewEvVariant(make="GAC Aion", model="UT", variant="Standard", price_with_coe=144988, coe_category="A", range_km=335, range_standard="WLTP", power_kw=100, battery_kwh=44.1, battery_warranty_years=8, listing_url=src, price_source_url="https://www.aion.sg", source="sample"),
        NewEvVariant(make="Tesla", model="Model Y", variant="RWD 110", price_with_coe=223127, coe_category="A", range_km=500, power_kw=110, battery_kwh=62.5, battery_warranty_years=8, listing_url="https://www.tesla.com/en_sg/modely", price_source_url="https://www.tesla.com/en_sg", source="sample"),
        NewEvVariant(make="Leapmotor", model="C10", variant="", price_with_coe=195999, coe_category="A", range_km=420, range_standard="WLTP", power_kw=160, battery_kwh=69.9, battery_warranty_years=8, listing_url=src, price_source_url=src, source="sample"),
        NewEvVariant(make="BYD", model="Dolphin", variant="", price_with_coe=165888, coe_category="A", range_km=345, power_kw=70, battery_kwh=50, battery_warranty_years=8, listing_url=src, price_source_url="https://www.byd.com/sg", source="sample"),
        NewEvVariant(make="BYD", model="M6", variant="7 seater", price_with_coe=171888, coe_category="A", range_km=420, power_kw=120, battery_kwh=71.8, battery_warranty_years=8, listing_url=src, price_source_url="https://www.byd.com/sg", source="sample"),
        NewEvVariant(make="BYD", model="Atto 2", variant="", price_with_coe=151388, coe_category="A", range_km=312, range_standard="WLTP", power_kw=130, battery_kwh=45.1, battery_warranty_years=8, listing_url=src, price_source_url="https://www.byd.com/sg", source="sample"),
        NewEvVariant(make="Xpeng", model="G6", variant="Standard Range", price_with_coe=213899, coe_category="B", range_km=435, range_standard="WLTP", power_kw=190, battery_kwh=66, battery_warranty_years=8, listing_url=src, price_source_url="https://www.xpeng.com/sg", source="sample"),
    ]
    from scrapers.new_ev import group_by_body_type, rank_new_evs

    new_ev_groups = group_by_body_type(list(new_evs), cfg)
    new_evs = rank_new_evs(new_evs, cfg)

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
        (CostBreakdown(label="Tesla Model 3 RWD 110 (new)", drivetrain="ev", road_tax=1678, insurance_low=1800, insurance_high=2800, depreciation=18000, energy=936, fixed_extras=2900), new_evs[0].listing_url),
        (CostBreakdown(label="BYD Atto 3 2023 (used)", drivetrain="ev", road_tax=1404, insurance_low=1800, insurance_high=2800, depreciation=11900, energy=936, fixed_extras=2900), used_ev[0][0].url),
        (CostBreakdown(label="Toyota Altis Hybrid 2021", drivetrain="hybrid", road_tax=742, insurance_low=1400, insurance_high=2300, depreciation=11200, energy=2351, fixed_extras=3300), used_ice[0][0].url),
    ]
    insurance_links = [(i["name"], i["url"]) for i in cfg["sources"]["insurance_comparison"]]
    fuel_note = (" Petrol 95 at 3.48 per litre (median of listed pump prices before discounts). "
                 "Cnergy today: 95 2.64 (member 2.54), 98 2.90 (member 2.80), diesel 1.80 (member 1.70). "
                 "Toyota Altis Hybrid 2021 fuelled at Cnergy member price: $2,096 a year instead of $2,871.")
    sources = [
        ("LTA OneMotoring COE results", cfg["sources"]["onemotoring_coe"]),
        ("Sgcarmart used cars", cfg["sources"]["sgcarmart_used_listing"]),
    ]
    sections = [
        summary_section(
            run_date, new_count=2, drop_count=2, gone_count=1,
            coe_line="Cat A $131,890 ▼1,119, Cat B $133,000 ▼2,000",
            best_pick=("BYD Atto 3 2023, 28,000 km, $118,800", used_ev[0][0].url, "Lowest depreciation per year in the used EV list with 7.4 years of COE left."),
        ),
        coe_section(tender, "sample tender", coe_rows, tender + timedelta(days=14), cfg["sources"]["onemotoring_coe"]),
        new_ev_section(new_evs, cfg=cfg, groups=new_ev_groups if cfg["new_ev"].get("group_by_body_type") else None),
        used_section("used_ev", used_ev, cfg=cfg),
        used_section("used_ice", used_ice, cfg=cfg),
        costs_section(costs, cfg["costs"]["insurance"]["assumptions"].strip() + fuel_note, insurance_links,
                      financing=[_fin(cfg, new_evs[0].price_with_coe, None, True), _fin(cfg, used_ev[0][0].price, used_ev[0][0].omv, False), _fin(cfg, used_ice[0][0].price, used_ice[0][0].omv, False)]),
    ]
    # Only the sections config.yaml asks for, in its order (Buying considerations is off by default).
    order = cfg["telegram"]["section_order"]
    sections = sorted((s for s in sections if s.key in order), key=lambda s: order.index(s.key))
    for s in sections:
        s.html = "<i>SAMPLE DATA, delivery test</i>\n" + s.html
    return sections
