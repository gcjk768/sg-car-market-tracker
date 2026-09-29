from datetime import date
from pathlib import Path

import pytest

from models import CoeCategory
from scrapers.coe import (
    SgcarmartCoeScraper,
    OneMotoringCoeScraper,
    exercise_label,
    find_tender_date,
    next_tender_date,
    parse_coe_tables,
    tender_result_dates,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def test_parse_rows_orientation():
    data = parse_coe_tables((FIX / "onemotoring_coe.html").read_text())
    assert data["A"] == {"quota": 1193, "bids": 1507, "successful": 1191, "premium": 131890}
    assert data["E"]["premium"] == 137000
    assert set(data) == set("ABCDE")


def test_parse_columns_orientation():
    data = parse_coe_tables((FIX / "sgcarmart_coe.html").read_text())
    assert data["B"]["premium"] == 133000
    assert data["C"]["quota"] == 318
    assert data["D"]["successful"] == 494


def test_tender_date_from_fixtures():
    today = date(2026, 9, 29)
    assert find_tender_date((FIX / "onemotoring_coe.html").read_text(), today) == date(2026, 9, 23)
    assert find_tender_date((FIX / "sgcarmart_coe.html").read_text(), today) == date(2026, 9, 23)


def test_scraper_parse_builds_models(cfg):
    s = OneMotoringCoeScraper(cfg, "test-agent", date(2026, 9, 29))
    results = s.parse((FIX / "onemotoring_coe.html").read_text())
    assert len(results) == 5
    a = next(r for r in results if r.category == CoeCategory.A)
    assert a.quota_premium == 131890 and a.tender_date == date(2026, 9, 23)
    assert a.exercise == "September 2026 second exercise"
    s2 = SgcarmartCoeScraper(cfg, "test-agent", date(2026, 9, 29))
    assert len(s2.parse((FIX / "sgcarmart_coe.html").read_text())) == 5


def test_exercise_label():
    assert exercise_label(date(2026, 9, 9)) == "September 2026 first exercise"
    assert exercise_label(date(2026, 9, 23)) == "September 2026 second exercise"


def test_tender_result_dates_september_2026():
    # First Monday of September 2026 is the 7th, third Monday is the 21st.
    assert tender_result_dates(2026, 9) == [date(2026, 9, 9), date(2026, 9, 23)]


@pytest.mark.parametrize(
    "after, expected",
    [
        (date(2026, 9, 23), date(2026, 10, 7)),   # result day itself rolls to next tender
        (date(2026, 9, 29), date(2026, 10, 7)),
        (date(2026, 10, 7), date(2026, 10, 21)),
        (date(2026, 12, 20), date(2026, 12, 23)),
        (date(2026, 12, 24), date(2027, 1, 6)),   # crosses the year
    ],
)
def test_next_tender_date(after, expected):
    assert next_tender_date(after) == expected
