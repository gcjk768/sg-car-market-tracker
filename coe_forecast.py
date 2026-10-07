"""When the next COE tender runs and what its premium may do, so new car prices can show a range
before the result is out. Assumptions live in config.yaml `coe.forecast` and `coe.bidding`."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Sequence

from models import CoeResult, NewEvVariant


@dataclass(frozen=True)
class Forecast:
    category: str
    last: int
    point: int
    low: int
    high: int

    @property
    def move(self) -> int:
        return self.high - self.point


def bidding_window(result_day: date, cfg: dict) -> tuple[datetime, datetime]:
    """Bidding opens on the Monday before the results Wednesday and closes on that Wednesday.
    ponytail: a public holiday in the week closes a day later, add a holiday calendar if it matters."""
    b = cfg["coe"]["bidding"]
    at = lambda day, hhmm: datetime.combine(day, time(*map(int, hhmm.split(":"))))
    return at(result_day - timedelta(days=2), b["opens"]), at(result_day, b["closes"])


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def forecast(history: Sequence[CoeResult], fc: dict) -> Forecast | None:
    """history is newest first. None when there are too few tenders to say anything."""
    rows = list(reversed(history[: fc["window"] + 1]))
    if len(rows) < 4:
        return None
    p = [r.quota_premium for r in rows]
    typical = statistics.median(abs(b - a) for a, b in zip(p, p[1:]))
    last = rows[-1]
    votes = _sign(p[-1] - p[-2])
    if last.bids_received and last.quota:
        votes += _sign(last.bids_received / last.quota - fc["demand_ratio"])
    point = round(p[-1] + fc["lean"] * votes / 2 * typical)
    return Forecast(last.category.value, p[-1], point, round(point - typical), round(point + typical))


def price_forecast(v: NewEvVariant, forecasts: dict[str, Forecast]) -> tuple[int, int, int] | None:
    """(low, expected, high) price of a new car at the next tender, assuming the dealer price carries
    the latest premium. None for a car whose COE category is unknown."""
    f = forecasts.get(v.coe_category.value) if v.coe_category else None
    if not f or not v.price_with_coe:
        return None
    return tuple(v.price_with_coe + x - f.last for x in (f.low, f.point, f.high))  # type: ignore[return-value]


if __name__ == "__main__":
    from datetime import date as d
    from models import CoeCategory

    def row(i: int, prem: int, bids: int = 1500, quota: int = 1000) -> CoeResult:
        return CoeResult(tender_date=d(2026, 1, 1) + timedelta(days=14 * i), exercise="x", category=CoeCategory.A,
                         quota_premium=prem, quota=quota, bids_received=bids, source="t")

    cfg = {"window": 24, "lean": 0.25, "demand_ratio": 1.2}
    rising = [row(5 - i, 100_000 + 1000 * (5 - i)) for i in range(6)]          # newest first, +1000 a tender
    f = forecast(rising, cfg)
    assert (f.last, f.point, f.low, f.high) == (105_000, 105_250, 104_250, 106_250), f
    falling_calm = [row(5 - i, 100_000 - 1000 * (5 - i), bids=900) for i in range(6)]   # falling, low demand
    assert forecast(falling_calm, cfg).point == 95_000 - 250
    assert forecast(rising[:3], cfg) is None
    v = NewEvVariant(make="X", model="Y", price_with_coe=200_000, coe_category=CoeCategory.A, listing_url="u", price_source_url="u", source="t")
    assert price_forecast(v, {"A": f}) == (199_250, 200_250, 201_250)
    print("coe_forecast ok")
