from datetime import date, datetime, timedelta

from coe_forecast import bidding_window, forecast, price_forecast
from models import CoeCategory, CoeResult, NewEvVariant

FC = {"window": 24, "lean": 0.25, "demand_ratio": 1.2}


def row(i, premium, bids=1500, quota=1000):
    return CoeResult(tender_date=date(2026, 1, 1) + timedelta(days=14 * i), exercise="x", category=CoeCategory.A,
                     quota_premium=premium, quota=quota, bids_received=bids, source="t")


def test_bidding_window_runs_monday_noon_to_wednesday_four(cfg):
    opens, closes = bidding_window(date(2026, 10, 21), cfg)
    assert opens == datetime(2026, 10, 19, 12, 0) and closes == datetime(2026, 10, 21, 16, 0)


def test_forecast_leans_with_momentum_and_demand_and_needs_history():
    rising = [row(5 - i, 100_000 + 1000 * (5 - i)) for i in range(6)]
    f = forecast(rising, FC)
    assert (f.last, f.point, f.low, f.high) == (105_000, 105_250, 104_250, 106_250)
    calm_fall = [row(5 - i, 100_000 - 1000 * (5 - i), bids=900) for i in range(6)]
    assert forecast(calm_fall, FC).point == 95_000 - 250
    assert forecast(rising[:3], FC) is None


def test_price_forecast_shifts_a_new_car_by_the_premium_change(cfg):
    f = forecast([row(5 - i, 100_000 + 1000 * (5 - i)) for i in range(6)], FC)
    v = NewEvVariant(make="X", model="Y", price_with_coe=200_000, coe_category=CoeCategory.A, listing_url="u", price_source_url="u", source="t")
    assert price_forecast(v, {"A": f}) == (199_250, 200_250, 201_250)
    assert price_forecast(v.model_copy(update={"coe_category": None}), {"A": f}) is None
