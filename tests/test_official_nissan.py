from pathlib import Path

from scrapers.official.nissan import parse, model_urls

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "official"
PAGES = ["ariya", "qashqai-launch", "serena", "x-trail", "noteepower"]


def _rows():
    rows = []
    for name in PAGES:
        rows.extend(parse((FIXTURES / f"nissan_{name}.html").read_text(encoding="utf-8"), name))
    return rows


def test_parse_nissan_model_pages():
    rows = _rows()
    triples = {(r.model, r.variant, r.price) for r in rows}
    assert len(rows) == 5
    assert ("Ariya EV", "ARIYA EV", 233800) in triples
    assert ("Qashqai", "QASHQAI PRESTIGE", 234800) in triples
    assert ("Serena e-POWER", "Serena e-POWER Highway Star Prestige Smart 8", 231800) in triples
    assert ("X-Trail e-POWER", "Premium", 256800) in triples
    assert ("Note e-POWER", "PREMIUM", 194800) in triples
    # Serena's unnamed grade LVL002 (234800) and Qashqai Premium (no price) are left out.
    assert 234800 not in {r.price for r in rows if r.model == "Serena e-POWER"}
    assert all(r.make == "Nissan" and r.includes_coe == "unknown" and not r.packages and r.list_price is None
               for r in rows)
    assert {r.model: r.fuel for r in rows} == {"Ariya EV": "ev", "Qashqai": "", "Serena e-POWER": "hybrid",
                                               "X-Trail e-POWER": "hybrid", "Note e-POWER": "hybrid"}
    assert all(r.note == "Nissan price feed updated 2026-10-08" for r in rows)


def test_model_urls_from_listing():
    urls = model_urls((FIXTURES / "nissan_new.html").read_text(encoding="utf-8"))
    assert len(urls) == 6
    assert "https://www.nissan.com.sg/vehicles/new/ariya.html" in urls
    assert "https://www.nissan.com.sg/vehicles/new/kicks.html" in urls


def test_page_without_feed_gives_no_rows():
    assert parse("<html><title>Nissan</title></html>") == []
