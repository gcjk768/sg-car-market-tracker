from pathlib import Path

from scrapers.official.skoda import parse

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "skoda_pricelist.html"


def test_parse_skoda_pricelist():
    rows = parse(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 11   # every block shows twice on the page; duplicates are dropped
    got = {(r.model, r.variant): r for r in rows}
    for model, variant, price, list_price in [
        ("Octavia", "Selection 1.5 TSI", 184900, 264900),
        ("Octavia Combi", "RS 2.0 TSI", 250900, 361900),
        ("Kodiaq", "Sportline 1.5 TSI", 228900, 321900),
        ("Kodiaq", "RS 2.0 TSI", 287900, 374900),
        ("Superb Combi", "1.5 TSI", 242900, 319900),
        ("Elroq", "63kWh 150kW", 217900, 259900),
        ("Enyaq", "63kWh 150kW", 227900, 277900),
    ]:
        r = got[(model, variant)]
        assert (r.price, r.list_price) == (price, list_price)
    assert all(r.includes_coe == "with" and r.packages == {} and r.make == "Skoda" for r in rows)
    assert got[("Elroq", "63kWh 150kW")].fuel == "ev"
    assert got[("Kamiq", "Selection 1.0 TSI")].fuel == "petrol"
    assert "7th October 2026 to 21st October 2026" in rows[0].note


def test_skoda_rows_reach_sgcarmart_variants(cfg):
    """Sgcarmart's names for the cars on the fixture page. "Octavia" and "Octavia Combi" share variant names, so only
    the closest official family may compete, else "Selection 1.5 TSI" ties between them and nothing matches."""
    from dataclasses import asdict

    import official
    from models import NewCarVariant

    rows = [asdict(r) for r in parse(FIXTURE.read_text(encoding="utf-8"))]
    want = {
        ("Octavia Mild Hybrid", "1.5 TSI Selection", 184900): ("Octavia", "Selection 1.5 TSI"),
        ("Octavia Combi Mild Hybrid", "1.5 TSI Selection", 199900): ("Octavia Combi", "Selection 1.5 TSI"),
        ("Octavia Combi RS", "2.0 TSI", 250900): ("Octavia Combi", "RS 2.0 TSI"),
        ("Octavia RS", "2.0 TSI", 229900): ("Octavia", "RS 2.0 TSI"),
        ("Kodiaq RS", "2.0 TSI 7-Seater", 287900): ("Kodiaq", "RS 2.0 TSI"),
        ("Kodiaq Mild Hybrid", "1.5 TSI Sportline 7-Seater", 228900): ("Kodiaq", "Sportline 1.5 TSI"),
        ("Superb Combi Mild Hybrid", "1.5 TSI Laurin & Klement", 242900): ("Superb Combi", "1.5 TSI"),
        ("Elroq", "Plus 63 kWh(A)", 217900): ("Elroq", "63kWh 150kW"),
        ("Scala", "1.0A TSI Ambition", 99800): None,   # not on the official page
    }
    vs = {k: NewCarVariant(make="Skoda", model=k[0], variant=k[1], price_with_coe=k[2], fuel="petrol",
                           listing_url="u", source="t") for k in want}
    found = official.match(list(vs.values()), rows, cfg)
    got = {k: (found[id(v)]["model"], found[id(v)]["variant"]) if id(v) in found else None for k, v in vs.items()}
    assert got == want
