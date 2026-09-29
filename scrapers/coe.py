"""COE tender results from LTA OneMotoring, with Sgcarmart and Motorist as fallbacks.

All three parsers share one table reader that copes with categories either down the rows or
across the columns, because every site lays the results out differently.
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from selectolax.parser import HTMLParser

from models import CoeCategory, CoeResult
from scrapers.base import BaseScraper, ScraperUnavailable
from scrapers.parse_utils import clean, parse_date, parse_int, parse_money, tables

log = logging.getLogger(__name__)

CATEGORY_RE = re.compile(r"\b(?:cat(?:egory)?\.?\s*)?([A-E])\b", re.I)
CATEGORY_ROW_RE = re.compile(r"^\s*(?:cat(?:egory)?\.?\s*)?([A-E])\b", re.I)


def _category_of(text: str) -> Optional[str]:
    m = CATEGORY_ROW_RE.match(clean(text))
    if m:
        return m.group(1).upper()
    return None


def _column_role(header: str) -> Optional[str]:
    h = header.lower()
    if "premium" in h or h in ("qp", "price") or "quota premium" in h:
        return "premium"
    if "success" in h:
        return "successful"
    if "received" in h or ("bid" in h and "success" not in h):
        return "bids"
    if "quota" in h:
        return "quota"
    if "cat" in h:
        return "category"
    return None


def parse_coe_tables(html: str) -> dict[str, dict[str, Any]]:
    """Return {category: {premium, quota, bids, successful}} from the first table that has
    categories and a premium column, in either orientation."""
    for headers, rows in tables(html):
        # Orientation 1: categories down the rows.
        roles = [_column_role(h) for h in headers]
        if "premium" in roles:
            cat_col = roles.index("category") if "category" in roles else 0
            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                if len(row) <= cat_col:
                    continue
                cat = _category_of(row[cat_col])
                if not cat:
                    continue
                entry: dict[str, Any] = {}
                for idx, role in enumerate(roles):
                    if role and role != "category" and idx < len(row):
                        entry[role] = parse_money(row[idx]) if role == "premium" else parse_int(row[idx])
                if entry.get("premium"):
                    result[cat] = entry
            if result:
                return result
        # Orientation 2: categories across the columns, measures down the rows.
        cats = [_category_of(h) for h in headers]
        if sum(1 for c in cats if c) >= 3:
            result = {}
            for row in rows:
                if not row:
                    continue
                role = _column_role(row[0])
                if not role:
                    continue
                for idx, cat in enumerate(cats):
                    if cat and idx < len(row):
                        value = parse_money(row[idx]) if role == "premium" else parse_int(row[idx])
                        result.setdefault(cat, {})[role] = value
            if any(v.get("premium") for v in result.values()):
                return {k: v for k, v in result.items() if v.get("premium")}
    return {}


def find_tender_date(html: str, today: date | None = None) -> Optional[date]:
    """Most recent date on the page that is not in the future. Prefers text near 'tender',
    'bidding' or 'exercise'."""
    today = today or date.today()
    text = clean(HTMLParser(html).body.text(separator=" ")) if HTMLParser(html).body else clean(html)
    candidates: list[date] = []
    for m in re.finditer(r"(?:tender|bidding|exercise|results?)[^.]{0,80}?(\d{1,2}[ -/]\w{3,9}[ -/]\d{4}|\d{4}-\d{2}-\d{2})", text, re.I):
        d = parse_date(m.group(1))
        if d and d <= today:
            candidates.append(d)
    if not candidates:
        for m in re.finditer(r"\d{1,2}[ -/]\w{3,9}[ -/]\d{4}|\d{4}-\d{2}-\d{2}", text):
            d = parse_date(m.group(0))
            if d and d <= today:
                candidates.append(d)
    # Snap to a real results day: headers such as "Results Sep 26 September 2026" otherwise
    # read as 26 September, a Saturday.
    return last_tender_on_or_before(max(candidates)) if candidates else None


def exercise_label(tender_date: date) -> str:
    ordinal = "first" if tender_date.day <= 15 else "second"
    return f"{tender_date.strftime('%B %Y')} {ordinal} exercise"


def _nth_monday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (calendar.MONDAY - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def tender_result_dates(year: int, month: int, weeks: tuple[int, ...] = (1, 3), results_weekday: int = 2) -> list[date]:
    """Result days (Wednesdays by default) for the tender weeks of a month."""
    out = []
    for n in weeks:
        monday = _nth_monday(year, month, n)
        out.append(monday + timedelta(days=results_weekday - calendar.MONDAY))
    return out


def next_tender_date(after: date, weeks: tuple[int, ...] = (1, 3), results_weekday: int = 2) -> date:
    """Next result day strictly after `after`. Public holidays can shift the real date by a day
    or two, which the report notes rather than models."""
    y, m = after.year, after.month
    for _ in range(4):
        for d in tender_result_dates(y, m, weeks, results_weekday):
            if d > after:
                return d
        m += 1
        if m > 12:
            m, y = 1, y + 1
    raise RuntimeError("no tender date found")


def last_tender_on_or_before(day: date, weeks: tuple[int, ...] = (1, 3), results_weekday: int = 2) -> date:
    """Latest scheduled result day on or before `day`."""
    y, m = day.year, day.month
    for _ in range(4):
        found = [d for d in tender_result_dates(y, m, weeks, results_weekday) if d <= day]
        if found:
            return max(found)
        m -= 1
        if m < 1:
            m, y = 12, y - 1
    raise RuntimeError("no tender date found")


class CoeScraperBase(BaseScraper):
    url_key = ""

    def url(self) -> str:
        return self.cfg["sources"][self.url_key]

    def parse(self, html: str) -> list[CoeResult]:
        data = parse_coe_tables(html)
        if not data:
            raise ScraperUnavailable(f"{self.name}: no COE table found")
        tender_date = find_tender_date(html, self.run_date) or self.run_date
        out = []
        for cat, entry in sorted(data.items()):
            if cat not in self.cfg["coe"]["store_categories"]:
                continue
            out.append(
                CoeResult(
                    tender_date=tender_date,
                    exercise=exercise_label(tender_date),
                    category=CoeCategory(cat),
                    quota_premium=entry["premium"],
                    quota=entry.get("quota"),
                    bids_received=entry.get("bids"),
                    bids_successful=entry.get("successful"),
                    source=self.name,
                    scraped_at=datetime.now(),
                )
            )
        if len(out) < 3:
            raise ScraperUnavailable(f"{self.name}: only {len(out)} categories parsed")
        return out

    def run(self) -> list[CoeResult]:
        return self.parse(self.fetch(self.url()))


class OneMotoringCoeScraper(CoeScraperBase):
    name = "onemotoring"
    url_key = "onemotoring_coe"

    def run(self) -> list[CoeResult]:
        # The LTA page loads its results with JavaScript, so try a rendered fetch first and fall
        # back to the static page in case the markup is server rendered after all.
        try:
            html = self.fetch_rendered(self.url(), wait_selector="table")
        except Exception as exc:  # playwright missing or timed out
            log.warning("onemotoring rendered fetch failed (%s), trying static", exc)
            html = self.fetch(self.url())
        return self.parse(html)


class SgcarmartCoeScraper(CoeScraperBase):
    name = "sgcarmart"
    url_key = "sgcarmart_coe_results"


class MotoristCoeScraper(CoeScraperBase):
    name = "motorist"
    url_key = "motorist_coe"

    # The row under the premiums: <i class="icon-arrow-down-2"></i> $1,119, one per category.
    CHANGE_RE = re.compile(r"icon-arrow-(up|down)[^$]{0,80}\$\s*([\d,]+)")

    def parse(self, html: str) -> list[CoeResult]:
        """Latest results plus the previous tender, worked out from the change row, so the report
        shows a change from the first run instead of waiting two weeks for history."""
        latest = super().parse(html)
        table = html[html.find("Quota Premium"):]
        changes = self.CHANGE_RE.findall(table)[: len(latest)]
        if len(changes) != len(latest):
            return latest
        prev_date = last_tender_on_or_before(latest[0].tender_date - timedelta(days=1))
        previous = []
        for r, (direction, amount) in zip(latest, changes):
            delta = parse_int(amount) or 0
            premium = r.quota_premium + delta if direction == "down" else r.quota_premium - delta
            previous.append(r.model_copy(update={"tender_date": prev_date, "exercise": exercise_label(prev_date),
                                                 "quota_premium": premium, "quota": None, "bids_received": None,
                                                 "bids_successful": None}))
        return latest + previous


SCRAPERS = {
    "onemotoring": OneMotoringCoeScraper,
    "sgcarmart": SgcarmartCoeScraper,
    "motorist": MotoristCoeScraper,
}


def scrape_coe(cfg: dict[str, Any], user_agent: str, run_date: date, force: bool = False) -> list[CoeResult]:
    """Try each configured source in order. Raises ScraperUnavailable if all fail."""
    errors = []
    for key in cfg["coe"].get("source_order", list(SCRAPERS)):
        scraper = SCRAPERS[key](cfg, user_agent, run_date, force)
        try:
            results = scraper.run()
            log.info("COE results from %s: %d rows for %s", key, len(results), results[0].tender_date)
            return results
        except Exception as exc:
            log.warning("COE source %s failed: %s", key, exc)
            errors.append(f"{key}: {exc}")
        finally:
            scraper.close()
    raise ScraperUnavailable("; ".join(errors))
