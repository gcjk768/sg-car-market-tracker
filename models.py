"""Pydantic models for every scraped record and for report rows."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class Drivetrain(str, Enum):
    ev = "ev"
    ice = "ice"
    hybrid = "hybrid"


class CoeCategory(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class CoeResult(BaseModel):
    """One category row from one COE tender exercise."""

    tender_date: date
    exercise: str = Field(description="Tender label, for example 2026-09 second exercise")
    category: CoeCategory
    quota_premium: int
    quota: Optional[int] = None
    bids_received: Optional[int] = None
    bids_successful: Optional[int] = None
    source: str
    scraped_at: datetime = Field(default_factory=datetime.now)


class NewEvVariant(BaseModel):
    make: str
    model: str
    variant: str = ""
    price_with_coe: Optional[int] = None
    price_without_coe: Optional[int] = None
    coe_category: Optional[CoeCategory] = None
    ves_band: Optional[str] = None
    ves_rebate: Optional[int] = Field(default=None, description="Positive for a rebate, negative for a surcharge")
    battery_kwh: Optional[float] = None
    range_km: Optional[int] = None
    range_standard: Optional[str] = Field(default=None, description="WLTP, NEDC, CLTC or unknown")
    power_kw: Optional[float] = None
    vehicle_warranty: Optional[str] = None
    battery_warranty: Optional[str] = None
    battery_warranty_years: Optional[float] = None
    promotion: Optional[str] = None
    price_includes_rebates: bool = Field(default=True, description="True when the price is net of VES and EEAI rebates")
    body_type: Optional[str] = Field(default=None, description="Hatchback, Sedan, SUV, MPV, Coupe, Wagon or Other")
    listing_url: str
    price_source_url: str
    source: str
    scraped_at: datetime = Field(default_factory=datetime.now)

    @property
    def score(self) -> Optional[float]:
        if self.price_with_coe and self.range_km:
            return self.price_with_coe / self.range_km
        return None

    @property
    def display_name(self) -> str:
        return " ".join(p for p in (self.make, self.model, self.variant) if p)


class UsedListing(BaseModel):
    source: str
    listing_id: str
    url: str
    make: str
    model: str
    variant: str = ""
    drivetrain: Drivetrain
    year: Optional[int] = None
    reg_date: Optional[date] = None
    mileage_km: Optional[int] = None
    owners: Optional[int] = None
    price: int
    depreciation_per_year: Optional[int] = None
    coe_expiry: Optional[date] = None
    coe_years_remaining: Optional[float] = None
    omv: Optional[int] = None
    arf: Optional[int] = None
    dereg_value: Optional[int] = Field(default=None, description="PARF plus COE rebate quoted by the site, if any")
    engine_cc: Optional[int] = None
    power_kw: Optional[float] = None
    seller_type: Optional[str] = Field(default=None, description="dealer or direct owner")
    battery_health: Optional[str] = None
    flags: list[str] = Field(default_factory=list, description="accident, as is, no warranty and similar")
    description: str = ""
    first_seen: Optional[date] = None
    last_seen: Optional[date] = None
    price_history: list["PricePoint"] = Field(default_factory=list)
    scraped_at: datetime = Field(default_factory=datetime.now)

    @field_validator("flags", mode="before")
    @classmethod
    def _dedupe_flags(cls, value):
        if value is None:
            return []
        seen: list[str] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        return seen

    @property
    def display_name(self) -> str:
        return " ".join(p for p in (self.make, self.model, self.variant) if p)

    @property
    def age_years(self) -> Optional[float]:
        if self.reg_date:
            return (date.today() - self.reg_date).days / 365.25
        if self.year:
            return max(0.0, date.today().year - self.year + 0.5)
        return None


class PricePoint(BaseModel):
    seen_on: date
    price: int


class FuelPrice(BaseModel):
    observed_on: date
    ron95_per_litre: float
    by_brand: dict[str, float] = Field(default_factory=dict, description="Listed 95 octane price per brand before discounts")
    station_prices: dict[str, dict[str, float]] = Field(default_factory=dict, description="Preferred station board: grade to public and member price")
    source: str
    scraped_at: datetime = Field(default_factory=datetime.now)


class CostBreakdown(BaseModel):
    """Annual cost of ownership figures for one car, all in SGD."""

    label: str
    drivetrain: Drivetrain
    road_tax: int
    insurance_low: int
    insurance_high: int
    depreciation: int
    energy: int
    fixed_extras: int

    @property
    def total_low(self) -> int:
        return self.road_tax + self.insurance_low + self.depreciation + self.energy + self.fixed_extras

    @property
    def total_high(self) -> int:
        return self.road_tax + self.insurance_high + self.depreciation + self.energy + self.fixed_extras


class Financing(BaseModel):
    """Deposit and instalment under the MAS loan to value rules, flat rate car loan."""

    price: int
    ltv: float
    deposit: int
    loan: int
    rate_flat: float
    tenure_years: int
    monthly: int
    short_tenure_years: int
    monthly_short: int


class ReportSection(BaseModel):
    """One Telegram message worth of content. Text is already HTML formatted."""

    key: str
    title: str
    html: str
    available: bool = True
