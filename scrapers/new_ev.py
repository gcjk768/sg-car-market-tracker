"""New EV price list from Sgcarmart with official brand pages as a cross check."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Optional
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from models import CoeCategory, NewEvVariant
from scrapers.base import BaseScraper, ScraperUnavailable
from scrapers.parse_utils import clean, first_match, label_values, parse_money, parse_number, tables, text_of

log = logging.getLogger(__name__)

MODEL_HREF = re.compile(r"CarCode=(?P<code>\d+)|/new-cars/(?:model/)?(?P<slug>[\w-]+)")
VES_RE = re.compile(r"VES\s*(?:band)?\s*:?\s*([ABC][12]?)\b", re.I)
WARRANTY_YEARS_RE = re.compile(r"(\d+)\s*(?:years?|yrs?)", re.I)


BODY_TYPES = (
    ("mpv", "MPV"), ("people mover", "MPV"), ("suv", "SUV"), ("crossover", "SUV"), ("hatch", "Hatchback"),
    ("coupe", "Coupe"), ("sedan", "Sedan"), ("saloon", "Sedan"), ("wagon", "Wagon"), ("estate", "Wagon"),
)


def detect_body_type(make: str, model: str, cfg: dict[str, Any], *texts: str | None) -> str:
    """Body type from config overrides first, then from any of the given texts, else Other."""
    name = f"{make} {model}".lower()
    overrides = cfg["new_ev"].get("body_type_overrides", {})
    for key in sorted(overrides, key=len, reverse=True):
        k = key.lower()
        if name == k or name.startswith(k + " "):
            return overrides[key]
    for text in texts:
        if not text:
            continue
        low = text.lower()
        for needle, value in BODY_TYPES:
            if needle in low:
                return value
    return "Other"


def coe_category_for_power(power_kw: float | None, cfg: dict[str, Any]) -> Optional[CoeCategory]:
    if power_kw is None:
        return None
    return CoeCategory.A if power_kw <= cfg["coe"]["cat_a_max_power_kw"] else CoeCategory.B


def warranty_years(text: str | None) -> Optional[float]:
    if not text:
        return None
    m = WARRANTY_YEARS_RE.search(text)
    return float(m.group(1)) if m else None


def _range_standard(text: str) -> str:
    low = text.lower()
    for std in ("wltp", "nedc", "cltc", "epa"):
        if std in low:
            return std.upper()
    return "unknown"


class SgcarmartNewEvScraper(BaseScraper):
    name = "sgcarmart_new"

    def __init__(self, cfg, user_agent, run_date=None, force=False):
        super().__init__(cfg, user_agent, run_date, force)
        self.index_url = cfg["sources"]["sgcarmart_new_cars"]
        self.max_models = cfg["new_ev"].get("max_model_pages", 40)

    # Index page: links to electric models

    def parse(self, html: str) -> list[dict[str, str]]:
        tree = HTMLParser(html)
        models: dict[str, dict[str, str]] = {}
        for a in tree.css("a[href]"):
            href = a.attributes.get("href") or ""  # a bare <a href> gives None
            m = MODEL_HREF.search(href)
            if not m:
                continue
            container = a.parent if a.parent is not None else a
            for _ in range(3):
                if container.parent is not None and len(text_of(container)) < 40:
                    container = container.parent
            blurb = text_of(container).lower()
            if "electric" not in blurb and "ev" not in blurb.split():
                continue
            slug = m.group("code") or m.group("slug")
            if slug not in models:
                models[slug] = {"slug": slug, "url": urljoin(self.index_url, href), "title": text_of(a) or slug}
        return list(models.values())

    # Model page: variants with prices

    def parse_model(self, html: str, model_url: str) -> list[NewEvVariant]:
        tree = HTMLParser(html)
        values = label_values(tree)
        title = text_of(tree.css_first("h1")) or ""
        make, model = _split_title(title)
        page_text = text_of(tree.body) if tree.body else clean(html)

        battery = parse_number(first_match(values, "battery capacity", "battery"))
        range_key = next((k for k in values if "range" in k or "wltp" in k), "")
        range_text = f"{range_key} {values.get(range_key, '')}".strip()
        range_km = parse_number(values.get(range_key, ""))
        power_text = first_match(values, "power", "max power", "motor") or ""
        power_kw = parse_number(power_text) if "kw" in power_text.lower() else None
        vehicle_warranty = first_match(values, "vehicle warranty", "warranty")
        battery_warranty = first_match(values, "battery warranty", "high voltage battery")
        promo = first_match(values, "promotion", "promo", "offer")
        body_type = detect_body_type(make, model, self.cfg, first_match(values, "type of vehicle", "body type", "body", "vehicle type"), title)
        ves = VES_RE.search(page_text)
        ves_band = ves.group(1).upper() if ves else None
        ves_rebate = self.cfg["new_ev"].get("ves_rebates", {}).get(ves_band) if ves_band else None

        variants: list[NewEvVariant] = []
        for headers, rows in tables(html):
            low = [h.lower() for h in headers]
            price_cols = [i for i, h in enumerate(low) if "price" in h]
            if not price_cols:
                continue
            with_coe = next((i for i in price_cols if "without" not in low[i]), price_cols[0])
            without_coe = next((i for i in price_cols if "without" in low[i]), None)
            power_col = next((i for i, h in enumerate(low) if "power" in h), None)
            range_col = next((i for i, h in enumerate(low) if "range" in h), None)
            for row in rows:
                if not row or len(row) <= with_coe:
                    continue
                price = parse_money(row[with_coe])
                if not price or price < 50000:
                    continue
                v_power = parse_number(row[power_col]) if power_col is not None and power_col < len(row) else power_kw
                v_range = parse_number(row[range_col]) if range_col is not None and range_col < len(row) else range_km
                variants.append(
                    NewEvVariant(
                        make=make, model=model, variant=row[0],
                        price_with_coe=price,
                        price_without_coe=parse_money(row[without_coe]) if without_coe is not None and without_coe < len(row) else None,
                        coe_category=coe_category_for_power(v_power, self.cfg),
                        ves_band=ves_band, ves_rebate=ves_rebate,
                        battery_kwh=battery, range_km=int(v_range) if v_range else None,
                        range_standard=_range_standard(range_text), power_kw=v_power,
                        vehicle_warranty=vehicle_warranty, battery_warranty=battery_warranty,
                        battery_warranty_years=warranty_years(battery_warranty), promotion=promo,
                        body_type=body_type, listing_url=model_url, price_source_url=model_url, source=self.name,
                        scraped_at=datetime.now(),
                    )
                )
            if variants:
                break
        if not variants:
            price = parse_money(first_match(values, "price"))
            if price:
                variants.append(
                    NewEvVariant(
                        make=make, model=model, variant="", price_with_coe=price,
                        coe_category=coe_category_for_power(power_kw, self.cfg), ves_band=ves_band,
                        ves_rebate=ves_rebate, battery_kwh=battery, range_km=int(range_km) if range_km else None,
                        range_standard=_range_standard(range_text), power_kw=power_kw,
                        vehicle_warranty=vehicle_warranty, battery_warranty=battery_warranty,
                        battery_warranty_years=warranty_years(battery_warranty), promotion=promo,
                        body_type=body_type, listing_url=model_url, price_source_url=model_url, source=self.name,
                    )
                )
        return variants

    def run(self) -> list[NewEvVariant]:
        models = self.parse(self.fetch(self.index_url))
        if not models:
            raise ScraperUnavailable("no electric models found on the new cars index, layout may have changed")
        out: list[NewEvVariant] = []
        for m in models[: self.max_models]:
            try:
                out.extend(self.parse_model(self.fetch(m["url"]), m["url"]))
            except Exception as exc:
                log.warning("new EV model page %s failed: %s", m["url"], exc)
        if not out:
            raise ScraperUnavailable("no variants parsed from any model page")
        return out


def _split_title(title: str) -> tuple[str, str]:
    from scrapers.used_common import split_make_model

    make, model, variant = split_make_model(re.sub(r"\b(electric|ev)\b", "", title, flags=re.I))
    return make, (model + " " + variant).strip() if variant and len(variant) <= 12 else model


class BrandPriceChecker(BaseScraper):
    """Fetch each brand's official page and check whether the Sgcarmart price appears on it."""

    name = "brand_check"

    def parse(self, html: str) -> list[int]:
        text = text_of(HTMLParser(html).body) if HTMLParser(html).body else html
        return sorted({parse_money(m) for m in re.findall(r"S?\$\s*\d{2,3},\d{3}", text) if parse_money(m) and parse_money(m) >= 60000})

    def run(self) -> list:
        return []

    def check(self, variants: list[NewEvVariant], tolerance: float = 0.015) -> dict[str, str]:
        """Returns {make: note}. Variants whose price matches a brand page figure get that page as
        their price source URL."""
        notes: dict[str, str] = {}
        pages = self.cfg["sources"].get("brand_pages", {})
        for make in sorted({v.make for v in variants}):
            url = next((u for brand, u in pages.items() if brand.lower() in make.lower() or make.lower() in brand.lower()), None)
            if not url:
                continue
            try:
                amounts = self.parse(self.fetch(url))
            except Exception as exc:
                notes[make] = f"brand page unavailable ({exc.__class__.__name__})"
                continue
            if not amounts:
                notes[make] = "brand page shows no prices"
                continue
            matched = 0
            for v in variants:
                if v.make != make or not v.price_with_coe:
                    continue
                if any(abs(a - v.price_with_coe) <= tolerance * v.price_with_coe for a in amounts):
                    v.price_source_url = url
                    matched += 1
            notes[make] = f"{matched} of {sum(1 for v in variants if v.make == make)} prices confirmed on brand page"
        return notes


def group_by_body_type(variants: list[NewEvVariant], cfg: dict[str, Any]) -> list[tuple[str, list[NewEvVariant]]]:
    """(body type, best value variants) in the configured order. Variants without a body type
    are classified from the overrides. Each group holds top_n_per_body_type plus always included
    models that belong to it."""
    ncfg = cfg["new_ev"]
    per_group = ncfg.get("top_n_per_body_type", 4)
    order = ncfg.get("body_type_order", ["Hatchback", "Sedan", "SUV", "MPV", "Coupe", "Wagon", "Other"])
    always = [a.lower() for a in ncfg.get("always_include", [])]
    buckets: dict[str, list[NewEvVariant]] = {}
    for v in variants:
        if v.score is None:
            continue
        bt = v.body_type or detect_body_type(v.make, v.model, cfg)
        v.body_type = bt
        buckets.setdefault(bt, []).append(v)
    out = []
    for bt in order + [b for b in buckets if b not in order]:
        items = buckets.get(bt)
        if not items:
            continue
        items.sort(key=lambda v: (v.score, -(v.battery_warranty_years or 0)))
        chosen = items[:per_group]
        for v in items[per_group:]:
            name = f"{v.make} {v.model}".lower()
            if any(a in name or name in a for a in always):
                chosen.append(v)
        out.append((bt, chosen))
    return out


def rank_new_evs(variants: list[NewEvVariant], cfg: dict[str, Any]) -> list[NewEvVariant]:
    """Top N by score plus the always included models, deduplicated, in score order."""
    scored = [v for v in variants if v.score is not None]
    scored.sort(key=lambda v: (v.score, -(v.battery_warranty_years or 0)))
    top_n = cfg["new_ev"]["top_n"]
    chosen = scored[:top_n]
    always = [a.lower() for a in cfg["new_ev"].get("always_include", [])]
    for v in scored[top_n:]:
        name = f"{v.make} {v.model}".lower()
        if any(a in name or name in a for a in always):
            chosen.append(v)
    seen = set()
    out = []
    for v in chosen:
        key = (v.make, v.model, v.variant)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def scrape_new_evs(cfg: dict[str, Any], user_agent: str, run_date: date, force: bool = False) -> tuple[list[NewEvVariant], dict[str, str]]:
    scraper = SgcarmartNewEvScraper(cfg, user_agent, run_date, force)
    try:
        variants = scraper.run()
    finally:
        scraper.close()
    checker = BrandPriceChecker(cfg, user_agent, run_date, force)
    try:
        notes = checker.check(variants)
    finally:
        checker.close()
    return variants, notes
