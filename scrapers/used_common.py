"""Shared logic for the used car scrapers.

Each site scraper only has to say how to recognise a listing link on a results page and how
its detail page labels the fields. Everything else (paging, detail fetching, label mapping,
make and model splitting, drivetrain detection) lives here.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, ClassVar, Optional
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from models import Drivetrain, UsedListing
from scrapers.base import BaseScraper, ScraperUnavailable
from scrapers.parse_utils import (
    clean,
    contains_any,
    first_match,
    label_values,
    parse_date,
    parse_int,
    parse_km,
    parse_money,
    parse_number,
    parse_years,
    text_of,
)

log = logging.getLogger(__name__)

KNOWN_MAKES = [
    "Alfa Romeo", "Aston Martin", "Audi", "BMW", "BYD", "Bentley", "Chery", "Citroen", "Cupra",
    "Deepal", "DS", "Ferrari", "Fiat", "Ford", "GAC Aion", "Aion", "Geely", "Genesis", "Honda",
    "Hyundai", "Jaguar", "Jeep", "Kia", "Lamborghini", "Land Rover", "Lexus", "Lotus", "MG",
    "Maserati", "Mazda", "Mercedes-Benz", "Mercedes", "Mini", "Mitsubishi", "Nissan", "Omoda",
    "Opel", "Peugeot", "Polestar", "Porsche", "Renault", "Rolls-Royce", "Seat", "Skoda", "Smart",
    "Subaru", "Suzuki", "Tesla", "Toyota", "Volkswagen", "Volvo", "Xpeng", "Zeekr", "Ora", "Lynk & Co",
    "Jaecoo", "Leapmotor", "Nio", "Maxus", "Perodua", "Proton", "Infiniti", "Rover", "Saab",
]
_MAKES_LOWER = sorted(((m.lower(), m) for m in KNOWN_MAKES), key=lambda t: -len(t[0]))

EV_WORDS = ("electric", " ev", "(ev)", "bev", "kwh")
HYBRID_WORDS = ("hybrid", "phev", "plug-in", "e:hev", "e-power", "mild hybrid")


def split_make_model(title: str) -> tuple[str, str, str]:
    """'BYD Atto 3 Electric Dynamic 60.5kWh' becomes ('BYD', 'Atto 3', 'Electric Dynamic 60.5kWh')."""
    t = clean(title)
    low = t.lower()
    make = ""
    for lm, m in _MAKES_LOWER:
        if low.startswith(lm + " ") or low == lm:
            make = m
            t = t[len(lm):].strip()
            break
    tokens = t.split()
    if not make:
        make = tokens[0] if tokens else ""
        tokens = tokens[1:]
    # Model is the first token plus a second one when it is short or numeric (Atto 3, Model 3, Ioniq 5).
    if not tokens:
        return make, "", ""
    model = tokens[0]
    rest = tokens[1:]
    if rest and (len(rest[0]) <= 2 or rest[0][0].isdigit() and len(rest[0]) <= 3):
        model += " " + rest[0]
        rest = rest[1:]
    return make, model, " ".join(rest)


def detect_drivetrain(text: str, default: Drivetrain) -> Drivetrain:
    low = " " + text.lower() + " "
    if any(w in low for w in HYBRID_WORDS):
        return Drivetrain.hybrid
    if any(w in low for w in EV_WORDS):
        return Drivetrain.ev
    return default


class UsedScraperBase(BaseScraper):
    """Walk result pages, open detail pages, return UsedListing models."""

    name: ClassVar[str] = "used"
    listing_href: ClassVar[re.Pattern] = re.compile(r"$^")
    needs_js: ClassVar[bool] = False
    # Site specific label synonyms, lower case, checked before the generic ones.
    labels: ClassVar[dict[str, tuple[str, ...]]] = {}

    GENERIC_LABELS: ClassVar[dict[str, tuple[str, ...]]] = {
        "price": ("price", "asking"),
        "depreciation": ("depreciation", "depre"),
        "reg_date": ("reg date", "registration date", "registered", "reg. date", "original reg"),
        "mileage": ("mileage", "odometer"),
        "owners": ("owner",),
        "coe": ("coe expiry", "coe left", "coe", "certificate of entitlement"),
        "omv": ("omv", "open market"),
        "arf": ("arf", "additional registration"),
        "dereg": ("dereg value", "deregistration value", "paper value", "dereg"),
        "engine_cc": ("engine cap", "engine capacity", "engine", "displacement"),
        "power": ("power", "motor power", "max power"),
        "fuel": ("fuel type", "fuel", "drivetrain"),
        "seller": ("seller type", "seller", "listed by", "sold by", "dealer"),
        "battery": ("battery health", "state of health", "soh", "battery report", "battery"),
        "year": ("manufactured", "year of manufacture", "year"),
        "description": ("description", "features", "remarks", "accessories", "vehicle description"),
    }

    def __init__(self, cfg: dict[str, Any], user_agent: str, run_date: date | None = None, force: bool = False, group: str = "ev"):
        super().__init__(cfg, user_agent, run_date, force)
        self.group = group
        self.default_drivetrain = Drivetrain.ev if group == "ev" else Drivetrain.ice
        used_cfg = cfg["used"]
        self.max_list_pages = used_cfg.get("max_list_pages", 3)
        self.max_details = used_cfg.get("max_detail_pages_per_search", 40)
        self.search_url = used_cfg["searches"][group]["urls"][self.name]
        self.flag_keywords = used_cfg["filters"]["exclude_keywords"]

    # Result pages

    def _get(self, url: str) -> str:
        return self.fetch_rendered(url) if self.needs_js else self.fetch(url)

    def parse(self, html: str) -> list[dict[str, Any]]:
        """Cards from a results page: listing_id, url, title, price and any inline fields."""
        tree = HTMLParser(html)
        cards: dict[str, dict[str, Any]] = {}
        for a in tree.css("a[href]"):
            href = a.attributes.get("href", "")
            m = self.listing_href.search(href)
            if not m:
                continue
            listing_id = m.group("id")
            if listing_id in cards:
                continue
            container = self._card_container(a)
            title = text_of(a) or text_of(container.css_first("h1, h2, h3, h4, .title"))
            card_text = text_of(container)
            card: dict[str, Any] = {
                "listing_id": listing_id,
                "url": urljoin(self.search_url, href),
                "title": title if len(title) > 3 else card_text[:80],
                "price": self._price_from_text(card_text),
                "text": card_text,
            }
            cards[listing_id] = card
        return list(cards.values())

    @staticmethod
    def _card_container(a: Node) -> Node:
        node = a
        for _ in range(6):
            if node.parent is None or node.parent.tag in ("body", "html"):
                break
            node = node.parent
            if "$" in text_of(node):
                return node
        return node

    @staticmethod
    def _price_from_text(text: str) -> Optional[int]:
        m = re.search(r"S?\$\s*\d[\d,]{4,}", text)
        return parse_money(m.group(0)) if m else None

    # Detail pages

    def _label(self, values: dict[str, str], key: str) -> Optional[str]:
        needles = self.labels.get(key, ()) + self.GENERIC_LABELS.get(key, ())
        return first_match(values, *needles)

    def parse_detail(self, html: str, card: dict[str, Any]) -> UsedListing:
        tree = HTMLParser(html)
        values = label_values(tree)
        page_text = text_of(tree.body) if tree.body else clean(html)
        title = text_of(tree.css_first("h1")) or card["title"]
        make, model, variant = split_make_model(title)

        price = parse_money(self._label(values, "price")) or card.get("price")
        if not price:
            raise ScraperUnavailable(f"{self.name}: no price for {card['url']}")
        reg_date = parse_date(self._label(values, "reg_date"))
        year = reg_date.year if reg_date else parse_int(self._label(values, "year"))
        if year and (year < 1990 or year > self.run_date.year + 1):
            year = None
        coe_text = self._label(values, "coe") or ""
        coe_expiry = parse_date(coe_text)
        coe_years = parse_years(coe_text)
        if coe_expiry and coe_years is None:
            coe_years = round((coe_expiry - self.run_date).days / 365.25, 2)
        if coe_expiry is None and coe_years is not None:
            coe_expiry = self.run_date.fromordinal(self.run_date.toordinal() + int(coe_years * 365.25))
        fuel_text = (self._label(values, "fuel") or "") + " " + title
        drivetrain = detect_drivetrain(fuel_text, self.default_drivetrain)
        power_text = self._label(values, "power")
        power_kw = parse_number(power_text) if power_text and "kw" in power_text.lower() else None
        if power_text and power_kw is None:
            bhp = parse_number(power_text)
            power_kw = round(bhp * 0.7457, 1) if bhp and "bhp" in power_text.lower() else None
        seller_text = (self._label(values, "seller") or "").lower()
        seller = "direct owner" if any(w in seller_text for w in ("direct", "owner", "private")) else ("dealer" if seller_text else None)
        description = self._label(values, "description") or ""
        flags = contains_any(page_text, self.flag_keywords)
        battery = self._label(values, "battery") if drivetrain == Drivetrain.ev else None

        return UsedListing(
            source=self.name,
            listing_id=card["listing_id"],
            url=card["url"],
            make=make,
            model=model,
            variant=variant,
            drivetrain=drivetrain,
            year=year,
            reg_date=reg_date,
            mileage_km=parse_km(self._label(values, "mileage")),
            owners=parse_int(self._label(values, "owners")),
            price=price,
            depreciation_per_year=parse_money(self._label(values, "depreciation")),
            coe_expiry=coe_expiry,
            coe_years_remaining=coe_years,
            omv=parse_money(self._label(values, "omv")),
            arf=parse_money(self._label(values, "arf")),
            dereg_value=parse_money(self._label(values, "dereg")),
            engine_cc=parse_int(self._label(values, "engine_cc")) if drivetrain != Drivetrain.ev else None,
            power_kw=power_kw,
            seller_type=seller,
            battery_health=battery,
            flags=flags,
            description=description[:2000],
        )

    # Orchestration

    def run(self) -> list[UsedListing]:
        cards: list[dict[str, Any]] = []
        for page in range(1, self.max_list_pages + 1):
            url = self.search_url.format(page=page)
            try:
                page_cards = self.parse(self._get(url))
            except Exception as exc:
                if page == 1:
                    raise ScraperUnavailable(f"{self.name} results page failed: {exc}") from exc
                log.warning("%s page %d failed: %s", self.name, page, exc)
                break
            if not page_cards:
                break
            cards.extend(page_cards)
        if not cards:
            raise ScraperUnavailable(f"{self.name}: no listings found on the results page, layout may have changed")
        listings: list[UsedListing] = []
        for card in cards[: self.max_details]:
            try:
                listings.append(self.parse_detail(self._get(card["url"]), card))
            except Exception as exc:
                log.warning("%s detail %s failed: %s", self.name, card["url"], exc)
        if not listings:
            raise ScraperUnavailable(f"{self.name}: every detail page failed to parse")
        return listings
