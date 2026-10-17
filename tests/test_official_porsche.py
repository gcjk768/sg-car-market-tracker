from pathlib import Path

from scrapers.official.porsche import parse

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "porsche_models.html"


def test_parse_porsche_models_page():
    rows = parse(FIXTURE.read_text(encoding="utf-8"))
    triples = {(r.model, r.variant, r.price) for r in rows}
    assert len(rows) == 86
    assert ("911 Carrera", "911 Carrera", 586988) in triples
    assert ("911 Carrera", "911 Carrera T", 637788) in triples
    assert ("911 Carrera Cabriolet", "911 Carrera T Cabriolet", 724088) in triples
    assert ("911 Carrera", "911 Carrera 4 GTS", 850088) in triples
    assert ("Taycan", "Taycan Turbo GT with Weissach Package", 1036388) in triples
    assert ("Cayenne Coupe", "Cayenne Turbo E-Hybrid Coupe with GT Package", 1001388) in triples
    # The page sells two different Macan GTS cars: the electric one and the petrol one.
    assert ("Macan Electric", "Macan GTS", 365988) in triples
    assert ("Macan", "Macan GTS", 406688) in triples
    assert all(r.make == "Porsche" and r.includes_coe == "without" and not r.packages for r in rows)
    fuel = {(r.model, r.variant): r.fuel for r in rows}
    assert fuel[("Taycan", "Taycan 4S")] == "ev"
    assert fuel[("Panamera", "Panamera 4 E-Hybrid")] == "hybrid"
    assert fuel[("718 Cayman", "718 Cayman GTS 4.0")] == "petrol"


def test_parse_without_model_list_is_empty():
    assert parse("<html></html>") == []
