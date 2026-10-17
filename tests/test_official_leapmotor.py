from pathlib import Path

from scrapers.official.leapmotor import BASE, models, parse

DIR = Path(__file__).resolve().parent.parent / "fixtures" / "official"
B10 = (DIR / "leapmotor_b10.html").read_text(encoding="utf-8")
C10 = (DIR / "leapmotor_c10.html").read_text(encoding="utf-8")


def test_models_from_menu_links():
    assert models(B10) == {BASE + "/sg/leapmotor/b10": "B10", BASE + "/sg/leapmotor/c10": "C10"}


def test_parse_leapmotor_model_pages():
    rows = parse({BASE + "/sg/leapmotor/b10": B10, BASE + "/sg/leapmotor/c10": C10})
    triples = {(r.model, r.variant, r.price) for r in rows}
    assert len(rows) == 2
    assert triples == {("B10", "", 180999), ("C10", "", 193999)}   # checked against the rendered price bars
    assert all(r.make == "Leapmotor" and r.includes_coe == "with" and r.fuel == "ev" and not r.packages
               and r.list_price is None and r.source_url.endswith(r.model.lower()) for r in rows)


def test_page_without_price_bar_is_left_out():
    assert parse({BASE + "/sg/leapmotor/x9": "<html><body>restocking</body></html>"}) == []
