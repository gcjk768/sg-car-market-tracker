from pathlib import Path

from scrapers.official import lexus

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "official" / "lexus_price_list.html"


def test_parse_lexus_price_list():
    rows = lexus.parse(FIX.read_text(encoding="utf-8"))
    got = {(r.model, r.variant): r for r in rows}
    assert len(rows) == 16
    for model, variant, price, fuel in [
        ("LBX", "LBX Cool", 275800, "hybrid"),
        ("NX", "NX 450h+ F SPORT", 456800, "hybrid"),
        ("ES", "ES 350e Pure Electric", 396800, "ev"),
        ("RZ", "RZ 350e Executive", 356800, "ev"),
        ("LS", "LS 500h", 842800, "hybrid"),
        ("LC", "LC 500", 847800, ""),
        ("LM", "LM 350h 7-Seater", 723800, "hybrid"),
    ]:
        r = got[(model, variant)]
        assert (r.price, r.fuel) == (price, fuel)
    assert all(r.includes_coe == "unknown" and r.packages == {} and r.make == "Lexus" for r in rows)
    assert not any(r.variant.startswith("IS") for r in rows)   # P.O.A, no price
    assert got[("LC", "LC 500h")].note == "" and got[("UX", "UX 300h")].note == "starting price ('From')"
