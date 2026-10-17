from pathlib import Path

import pytest

from scrapers.base import FetchError
from scrapers.official import Blocked
from scrapers.official.zeekr import URL, parse, scrape

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "zeekr_7x.html"
EXPECTED = [
    ("7X", "RWD Standard", 279999),
    ("7X", "RWD Long Range", 301999),
    ("7X", "AWD Performance", 329999),
]


def test_parse_zeekr_7x():
    rows = parse(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 3
    assert [(r.model, r.variant, r.price) for r in rows] == EXPECTED
    assert all(r.make == "Zeekr" and r.includes_coe == "unknown" and r.packages == {} and r.list_price is None
               and r.fuel == "ev" and r.source_url == URL for r in rows)
    assert rows[0].note == "Price valid till 6 October 2026."


def test_page_without_prices():
    assert parse("<html><p>Zeekr 009</p><p>582 km Cruising Range (WLTP)</p></html>") == []


class _Pages:
    def __init__(self, pages, fail=None):
        self.pages, self.fail, self.calls = pages, fail or {}, []

    def page(self, url):
        self.calls.append(url)
        if url in self.fail:
            raise self.fail[url]
        return self.pages.get(url, "<html>Zeekr model page without prices</html>")


X = "https://www.zeekrlife.com/en-sg/models/x/"
NINE = "https://www.zeekrlife.com/en-sg/models/009/"


@pytest.mark.parametrize("exc", [FetchError(NINE + ": 429"), Blocked(NINE)])
def test_failed_extra_page_keeps_7x_rows(exc):
    stub = _Pages({URL: FIXTURE.read_text(encoding="utf-8")}, fail={NINE: exc})
    rows = scrape(stub)
    assert [(r.model, r.variant, r.price) for r in rows] == EXPECTED
    assert set(stub.calls) == {URL, X, NINE}


def test_first_failure_stops_further_reads():
    stub = _Pages({URL: FIXTURE.read_text(encoding="utf-8")}, fail={X: FetchError(X), NINE: FetchError(NINE)})
    assert len(scrape(stub)) == 3
    assert len(stub.calls) == 2


def test_blocked_first_page_raises():
    with pytest.raises(Blocked):
        scrape(_Pages({}, fail={URL: Blocked(URL)}))
