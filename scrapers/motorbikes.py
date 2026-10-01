"""Motorbikes: used bikes per licence class from SGBikemart, best selling brands from LTA table M04.

Licence classes: 2B up to 200cc, 2A 201 to 400cc, 2 above 400cc. SGBikemart filters its used
listings by class and its result cards carry everything the report needs (reg date, cc, type,
mileage, price), so no detail page is opened. New bike prices are not scraped: dealer ads mix
full prices, deposits and instalments, with or without COE.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from scrapers.base import ScraperUnavailable
from scrapers.registrations import RegistrationsScraper

log = logging.getLogger(__name__)

CLASSES = ("2B", "2A", "2")
BASE = "https://sgbikemart.com.sg"


@dataclass
class UsedBike:
    title: str
    url: str
    licence_class: str
    price: int
    reg_date: Optional[date]
    cc: Optional[int]
    bike_type: str
    mileage_km: Optional[int]
    electric: bool = False

    def coe_years_left(self, today: date, coe_years: int = 10) -> Optional[float]:
        """A bike COE runs 10 years from registration. A renewed COE is not on the card, so older bikes read as expired."""
        if not self.reg_date:
            return None
        return round(coe_years - (today - self.reg_date).days / 365.25, 1)

    def depreciation(self, today: date) -> Optional[int]:
        left = self.coe_years_left(today)
        return int(self.price / left) if left and left > 0 else None


_CARD = re.compile(r'href="(/listing/usedbike/[^"]+/\d+/)">\s*<h3 class="mb-0">([^<]+)</h3>(.*?)(?=<h3 class="mb-0">|$)', re.S)
_INT = re.compile(r"[\d,]+")


def _field(text: str, label: str) -> str:
    m = re.search(rf"{label}\s*:\s*([^|]+)", text)
    return m.group(1).strip() if m else ""


def _num(s: str) -> Optional[int]:
    m = _INT.search(s or "")
    return int(m.group(0).replace(",", "")) if m and m.group(0).replace(",", "") else None


def parse_used_bikes(page: str, licence_class: str) -> list[UsedBike]:
    """Result cards of one SGBikemart used bike page. A card without a price is skipped."""
    bikes = []
    for href, title, body in _CARD.findall(page):
        text = " | ".join(t.strip() for t in html_lib.unescape(re.sub(r"<[^>]+>", "|", body)).split("|") if t.strip())
        text = text.replace("| :", ":").replace(": |", ":")
        price = re.search(r"SGD\s*\|?\s*\$\s?([\d,]+)", text)
        if not price:
            continue
        reg = re.search(r"Reg Date\s*:\s*(\d{2})/(\d{2})/(\d{4})", text)
        title = html_lib.unescape(title).strip()
        cc = _num(_field(text, "Capacity"))
        bikes.append(UsedBike(
            title=title, url=BASE + href, licence_class=licence_class, price=int(price.group(1).replace(",", "")),
            reg_date=date(int(reg.group(3)), int(reg.group(2)), int(reg.group(1))) if reg else None,
            cc=cc, bike_type=_field(text, "Vehicle Type"), mileage_km=_num(_field(text, "Mileage")),
            electric=bool(re.search(r"(?i)\belectric\b|\bev\b", title)) or cc == 0,
        ))
    return bikes


def shortlist(bikes: list[UsedBike], cfg: dict[str, Any], today: date) -> list[UsedBike]:
    """Bikes with enough COE left and believable mileage, lowest depreciation per year first."""
    f = cfg["motorbikes"]["filters"]
    keep = []
    for b in bikes:
        left = b.coe_years_left(today)
        if left is None or left < f["min_coe_years_remaining"] or b.price > f["price_ceiling_sgd"]:
            continue
        age = 10 - left
        if b.mileage_km is not None and b.mileage_km > f["max_km_per_year_of_age"] * max(age, 0.5):
            continue
        keep.append(b)
    seen, out = set(), []
    for b in sorted(keep, key=lambda b: (b.depreciation(today) or 10**9, b.mileage_km or 0)):
        if b.url not in seen:
            seen.add(b.url)
            out.append(b)
    return out[: cfg["motorbikes"]["top_n_per_class"]]


ROW = re.compile(r"^(?P<make>[A-Z][A-Z0-9 .&'/-]*?) (?P<fuel>Petrol-Electric|Petrol|Electric|Diesel)\b(?P<nums>(?: [\d,]+)+)$")
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def parse_mc_registrations(text: str) -> tuple[dict[str, dict[str, int]], list[str]]:
    """({make: {"total": n, "ev": n, "petrol": n}}, months covered) from LTA table M04's text.
    Each row is make, fuel, the months that had registrations, then the year's total last."""
    makes: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        make = m["make"].replace(".", "").strip()
        row = makes.setdefault(make, {"total": 0, "ev": 0, "petrol": 0})
        n = int(m["nums"].split()[-1].replace(",", ""))
        row["total"] += n
        row["ev" if m["fuel"] == "Electric" else "petrol"] += n
    year = re.search(r"\(\s*(20\d\d)\s*\)", text)
    header = next((l for l in text.splitlines() if l.startswith("Make Fuel Type")), "")
    months = [f"{year.group(1)}-{_MONTHS.index(w) + 1:02d}" for w in header.split() if w in _MONTHS] if year else []
    return makes, months


class BikeRegistrationsScraper(RegistrationsScraper):
    name = "mc_registrations"

    def parse(self, text: str) -> list:
        makes, months = parse_mc_registrations(text)
        if not makes:
            raise ScraperUnavailable("no make rows found in LTA table M04")
        return [makes, months]

    def run(self) -> list:
        return self.parse(self.pdf_text(self.cfg["sources"]["lta_mc_registrations_by_make"]))


class UsedBikeScraper(RegistrationsScraper):
    """Only for fetch(): robots, throttle and the daily cache come from BaseScraper."""
    name = "used_bikes"

    def run(self) -> list:
        m = self.cfg["motorbikes"]
        year_min = self.run_date.year - (10 - int(m["filters"]["min_coe_years_remaining"]))
        out: dict[str, list[UsedBike]] = {}
        for cls in CLASSES:
            bikes: list[UsedBike] = []
            for page in range(1, m["max_list_pages"] + 1):
                url = m["search_url"].format(licence_class=cls, year_min=year_min, page=page)
                found = parse_used_bikes(self.fetch(url), cls)
                bikes += found
                if not found:
                    break
            out[cls] = bikes
        if not any(out.values()):
            raise ScraperUnavailable("sgbikemart: no used bike cards parsed")
        return [out]


def scrape_motorbikes(cfg: dict[str, Any], user_agent: str, run_date: date, force: bool = False) -> dict[str, Any]:
    """{"used": {class: [UsedBike]}, "brands": (makes, months) or None}. Each part fails on its own."""
    result: dict[str, Any] = {"used": {}, "brands": None}
    for key, cls in (("used", UsedBikeScraper), ("brands", BikeRegistrationsScraper)):
        scraper = cls(cfg, user_agent, run_date, force)
        try:
            got = scraper.run()
            result[key] = got[0] if key == "used" else tuple(got)
        except Exception as exc:
            log.warning("motorbikes %s unavailable: %s", key, exc)
        finally:
            scraper.close()
    return result
