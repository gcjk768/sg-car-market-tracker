"""Kia's price list PDF, parsed offline from a saved layout text copy (valid 08 to 21 October 2026)."""
from pathlib import Path

from scrapers.official import kia

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "official"


def _rows():
    return kia.parse((FIX / "kia_pricelist.txt").read_text(encoding="utf-8"), "https://example/kia.pdf")


def test_finds_current_pdf_from_redirect_page():
    html = (FIX / "kia_request_pricelist.html").read_text(encoding="utf-8")
    assert kia.find_pdf(html) == ("https://www.kia.com/content/dam/kwcms/sg/en/pdf/Service/Pricelist/"
                                  "KiaRangePricelist_08Oct26.pdf")


def test_parses_every_row_with_the_right_pairing():
    rows = _rows()
    assert len(rows) == 14
    got = {(r.model, r.variant): r for r in rows}
    # Checked by eye against the PDF: (model, variant, price paid = cheapest package)
    for model, variant, price in [
        ("Niro Hybrid", "Air", 199_999),
        ("Sorento Hybrid", "SXTP 7-Seater (Limited Time only)", 251_999),
        ("Carnival Hybrid", "8-Seater CLEARANCE (Limited Time only)", 283_999),
        ("EV5", "Earth Long Range", 221_999),
        ("EV4", "2027 Indent", 239_999),
    ]:
        assert got[(model, variant)].price == price
    niro = got[("Niro Hybrid", "Air")]
    assert niro.list_price == 266_999
    assert niro.packages == {"Platinum": 207_999, "Gold": 203_999, "Standard": 199_999}
    assert niro.fuel == "hybrid" and "08 to 21 October 2026" in niro.note and "Category A" in niro.note
    assert got[("Carnival Hybrid", "7-Seater")].packages == {"Platinum": 309_999, "Standard": 305_999}
    ev4 = got[("EV4", "Clearance stock")]
    assert (ev4.list_price, ev4.packages, ev4.fuel) == (259_999, {"Platinum": 213_999, "Standard": 209_999}, "ev")
    assert all(r.make == "Kia" for r in rows)
    assert all(r.includes_coe == "with" for r in rows if r.model != "EV9")


def test_ev9_body_only_price_is_the_net_without_coe_figure():
    ev9 = next(r for r in _rows() if r.model == "EV9")
    assert ev9.variant == "Dual Motor Long Range GT-Line (Body Only)"
    # 345,999 list minus 30,000, 8,000 and 8,000 discounts, printed once and marked "(Without COE)"
    assert (ev9.price, ev9.list_price, ev9.packages) == (299_999, 345_999, {"Package (without COE)": 299_999})
    assert ev9.includes_coe == "without"
    assert "Body Only" in ev9.note
