"""Filtering, ranking and tagging of used listings. All thresholds come from config.yaml."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any, Iterable, Sequence

from models import Drivetrain, UsedListing
from telegram_bot import fmt_delta


# "accident free", "no accidents", "non accident" and "never in an accident" are reassurances,
# not warnings. They are removed before the exclusion keywords are checked.
_NEGATED = re.compile(
    r"\b(?:no|non|zero|without|never (?:been )?in an?|never had an?)[\s-]*accidents?\b|\baccidents?[\s-]*free\b",
    re.I,
)


def strip_negated(text: str) -> str:
    return _NEGATED.sub(" ", text or "")


def _age_years(listing: UsedListing, today: date) -> float | None:
    if listing.reg_date:
        return max(0.0, (today - listing.reg_date).days / 365.25)
    if listing.year:
        return max(0.0, today.year - listing.year + 0.5)
    return None


def reject_reason(listing: UsedListing, cfg: dict[str, Any], today: date) -> str | None:
    """Return why a listing fails the filters, or None if it passes."""
    f = cfg["used"]["filters"]
    if "commercial vehicle" in listing.flags:
        return "commercial vehicle"
    if listing.price > f["price_ceiling_sgd"]:
        return "price above ceiling"
    if listing.mileage_km is None:
        return "missing mileage" if f.get("require_mileage", True) else None
    age = _age_years(listing, today)
    if age is None:
        return "missing age"
    kind = "ev" if listing.drivetrain == Drivetrain.ev else ("hybrid" if listing.drivetrain == Drivetrain.hybrid else "ice")
    if age >= f["max_age_years"][kind]:
        return "too old"
    if listing.mileage_km > f["max_km_per_year_of_age"] * max(age, 0.5):
        return "mileage too high for age"
    if listing.owners is not None and listing.owners > f["max_owners"]:
        return "too many owners"
    if listing.coe_years_remaining is None:
        return "missing COE"
    if listing.coe_years_remaining < f["min_coe_years_remaining"]:
        return "COE too short"
    haystack = strip_negated(listing.description + " " + " ".join(listing.flags)).lower()
    for word in f["exclude_keywords"]:
        if word.lower() in haystack:
            return f"excluded keyword: {word}"
    return None


def has_bonus(listing: UsedListing, cfg: dict[str, Any]) -> bool:
    haystack = listing.description.lower()
    return any(w.lower() in haystack for w in cfg["used"]["filters"].get("bonus_keywords", []))


def score(listing: UsedListing, cfg: dict[str, Any]) -> tuple[float, float]:
    """Sort key: adjusted depreciation per year, then mileage. Lower is better."""
    dep = listing.depreciation_per_year if listing.depreciation_per_year is not None else 10**9
    if has_bonus(listing, cfg):
        dep *= 1 - cfg["used"]["filters"].get("bonus_score", 0)
    return (dep, listing.mileage_km if listing.mileage_km is not None else 10**9)


def brand_count(make: str, brand_sales: dict[str, int]) -> int:
    """New registrations of the listing's brand, matching LTA's spelling of the make."""
    from scrapers.new_ev import make_matches

    return max((n for m, n in brand_sales.items() if make_matches(m, make)), default=0)


def brand_rank(make: str, brand_sales: dict[str, int]) -> int | None:
    """1 for the best selling brand, None when the brand sold none."""
    n = brand_count(make, brand_sales)
    if not n:
        return None
    return 1 + sum(1 for v in brand_sales.values() if v > n)


def sales_key(listing: UsedListing, cfg: dict[str, Any], brand_sales: dict[str, int]) -> tuple:
    """Best selling brand first, then the value score within the brand."""
    return (-brand_count(listing.make, brand_sales), score(listing, cfg))


def shortlist(listings: Iterable[UsedListing], cfg: dict[str, Any], today: date, top_n: int | None = None,
              brand_sales: dict[str, int] | None = None) -> tuple[list[UsedListing], Counter]:
    """Apply the filters, rank, and return (top listings, rejection counts by reason).

    With brand_sales ({LTA make: new registrations}) the cars of the best selling brands come
    first and the value score only orders cars within a brand. Without it, value order.
    """
    kept: list[UsedListing] = []
    rejected: Counter = Counter()
    for l in listings:
        reason = reject_reason(l, cfg, today)
        if reason:
            rejected[reason] += 1
        else:
            kept.append(l)
    if brand_sales:
        kept.sort(key=lambda l: sales_key(l, cfg, brand_sales))
    else:
        kept.sort(key=lambda l: score(l, cfg))
    n = top_n or cfg["used"]["top_n"]
    return kept[:n], rejected


def tag_for(listing: UsedListing, since: date) -> str:
    """NEW when first seen on or after `since`, DROP with the delta when the price fell since then."""
    if listing.first_seen and listing.first_seen >= since:
        return "NEW"
    before = [p for p in listing.price_history if p.seen_on < since]
    if before:
        previous = before[-1].price
        if listing.price < previous:
            return "DROP " + fmt_delta(listing.price - previous)
    return ""


def tagged(listings: Sequence[UsedListing], since: date) -> list[tuple[UsedListing, str]]:
    return [(l, tag_for(l, since)) for l in listings]
