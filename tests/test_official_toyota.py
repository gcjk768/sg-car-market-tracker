from pathlib import Path

import pytest

from scrapers.official import Blocked, toyota

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "toyota_models.html"

_CARD = ('<div data-test="context:car:1"><h2 data-test="text:model_title">{}</h2>'
         '<span data-test="text:price:finance-price-value">$1,696</span>{}</div>')


def test_parse_toyota_models():
    rows = toyota.parse(FIXTURE.read_text(encoding="utf-8"))
    got = {(r.model, r.variant, r.price) for r in rows}
    assert len(rows) == 10
    assert {("bZ4X", "", 225888), ("Corolla Altis", "", 197888), ("Corolla Cross", "", 225888),
            ("Camry Hybrid", "", 262888), ("RAV4 Hybrid", "", 251888), ("Vios Hybrid", "", 199888),
            ("Noah Hybrid", "", 224888), ("Harrier Hybrid", "", 262888)} <= got
    assert all(r.make == "Toyota" and r.includes_coe == "unknown" and not r.packages and r.list_price is None
               for r in rows)
    fuel = {r.model: r.fuel for r in rows}
    assert fuel["Sienta Hybrid"] == "hybrid" and fuel["bZ4X"] == ""


def test_card_without_price_is_skipped():
    text = (_CARD.format("ORPHAN", "") +
            _CARD.format("Vios Hybrid", '<span data-test="text:price:primary">$199,888</span>'))
    assert [(r.model, r.price) for r in toyota.parse(text)] == [("Vios Hybrid", 199888)]


class _Browser:
    def __init__(self, text):
        self.text = text

    def rendered(self, url, wait_selector=None):
        return self.text


def test_scrape_returns_fixture_rows():
    assert len(toyota.scrape(_Browser(FIXTURE.read_text(encoding="utf-8")))) == 10


def test_scrape_raises_blocked_on_challenge_page():
    with pytest.raises(Blocked):
        toyota.scrape(_Browser("<title>Just a moment...</title>Verify you are human by completing the action below."))


def test_scrape_fails_loudly_on_layout_change():
    with pytest.raises(ValueError):
        toyota.scrape(_Browser('<div class="car"><h2>COROLLA ALTIS</h2>From S$197,888 (new layout)</div>'))
