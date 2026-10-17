from pathlib import Path

from scrapers.official.polestar import parse

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "polestar_offers.html"


def test_parse_polestar_offers_page():
    rows = parse(FIXTURE.read_text(encoding="utf-8"))
    triples = {(r.model, r.variant, r.price) for r in rows}
    assert len(rows) == 3
    assert ("Polestar 2", "Polestar 2 SRSM Management Unit", 209999) in triples
    assert ("Polestar 3", "Polestar 3 LRDM Management Unit", 339999) in triples
    assert ("Polestar 4", "Polestar 4 LRDM Management Unit", 269999) in triples
    assert ("Polestar 4", "Polestar 4 LRDM Management Unit", 2288) not in triples   # instalment, not a price
    assert all(r.make == "Polestar" and r.includes_coe == "unknown" and not r.packages and r.list_price is None
               and r.fuel == "ev" and "Management unit" in r.note for r in rows)
