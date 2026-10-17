from pathlib import Path

import pytest

from scrapers.official.subaru import parse, scrape

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "subaru_pricing.html"


def test_parse_subaru_pricing_page():
    rows = parse(FIXTURE.read_text(encoding="utf-8"))
    triples = {(r.model, r.variant, r.price) for r in rows}
    assert len(rows) == 5
    assert ("E-Outback", "E-Outback", 221800) in triples
    assert ("Solterra", "Solterra XT", 219800) in triples
    assert ("Forester", "Forester Strong Hybrid", 199800) in triples
    assert ("BRZ", "BRZ 2.0 6MT", 271800) in triples
    assert ("BRZ", "BRZ 2.0 6AT", 276800) in triples
    assert all(r.make == "Subaru" and r.includes_coe == "unknown" and not r.packages for r in rows)
    assert {r.variant: r.fuel for r in rows}["Forester Strong Hybrid"] == "hybrid"


class _FakeScraper:
    def __init__(self, page: str):
        self._page = page

    def page(self, url: str) -> str:
        return self._page


def test_scrape_returns_rows_from_fixture():
    assert len(scrape(_FakeScraper(FIXTURE.read_text(encoding="utf-8")))) == 5


def test_scrape_raises_when_layout_changes():
    with pytest.raises(ValueError, match="layout changed"):
        scrape(_FakeScraper("<html><h3 class='title-5'>E-Outback</h3></html>"))
