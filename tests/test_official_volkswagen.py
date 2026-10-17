"""Volkswagen official price list parsed from a saved live copy (2026-10-17)."""
from pathlib import Path

from scrapers.official import volkswagen

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "volkswagen_price_list.html"


def test_parse_volkswagen_price_list():
    rows = volkswagen.parse(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 15   # 8 passenger cars, 7 electric; 3 commercial vans left out
    got = {(r.model, r.variant): r for r in rows}
    triples = {(r.model, r.variant, r.price) for r in rows}
    assert ("Golf", "Mk8.5 Life Plus 1.5 eTSI Mild Hybrid (85kW)", 195900) in triples
    assert ("Golf R", "2.0 TSI 245kW", 329900) in triples
    assert ("T-Cross", "R-Line 1.0 TSI", 198900) in triples
    assert ("ID. Buzz", "LWB 7-Seater 210 kW", 311900) in triples
    assert ("ID.4", "210 kW", 237900) in triples
    assert ("ID.5 GTX", "250 kW", 247900) in triples
    assert got[("Golf R", "2.0 TSI 245kW")].list_price == 426400
    assert got[("ID.4", "210 kW")].list_price == 306900
    assert got[("ID.4", "210 kW")].fuel == "ev"
    assert got[("Golf GTI", "2.0 TSI 195kW")].fuel == "petrol"
    assert got[("Tiguan", "Life 1.5 eTSI Mild Hybrid")].fuel == "hybrid"
    assert all(r.includes_coe == "with" and r.packages == {} and r.make == "Volkswagen" for r in rows)
    assert not any("Cargo" in r.model or "Crafter" in r.variant for r in rows)
