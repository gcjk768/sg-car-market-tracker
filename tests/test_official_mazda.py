from datetime import date
from pathlib import Path

from scrapers.official import mazda

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "official"
PKG = "Effective price (8 bid non guaranteed COE plan)"


def _rows(name, today=None):
    return mazda.parse((FIX / name).read_text(encoding="utf-8"), "https://mazda.com.sg/pdf/x.pdf", today)


def test_cat_a_and_b_rows_pair_model_variant_and_price():
    rows = _rows("mazda_cat_a.txt") + _rows("mazda_cat_b.txt")
    assert len(rows) == 9   # P.O.A. rows (Mazda 6, CX-30, CX-80 6 seater, MX-5 RF) are left out
    got = {(r.model, r.variant): (r.list_price, r.price) for r in rows}
    assert got[("Mazda 2", "1.5L Hatchback Sports")] == (230888, 187888)
    assert got[("Mazda 3 Sedan M-Hybrid", "1.5L Sedan Luxury")] == (253888, 210888)
    assert got[("Mazda 3 Hatchback M-Hybrid", "1.5L Hatchback Astina")] == (257888, 214888)
    assert got[("Mazda CX-5 M-Hybrid", "2.0L Luxury Sports")] == (312888, 264888)
    assert got[("Mazda CX-60", "2.5L Luxury")] == (369888, 321888)
    assert got[("Mazda CX-80 PHEV", "2.5L Luxury Sports (7-seater)")] == (409888, 361888)
    cx80 = next(r for r in rows if r.model == "Mazda CX-80 PHEV")
    assert cx80.packages == {PKG: 361888} and cx80.includes_coe == "with" and cx80.fuel == "hybrid"
    assert all(r.make == "Mazda" for r in rows)
    assert "valid 06-Aug-2026 to 19-Aug-2026" in rows[0].note and "$5,000" in rows[0].note


def test_expired_list_gives_no_rows():
    assert _rows("mazda_cat_a.txt", date(2026, 8, 19))
    assert _rows("mazda_cat_a.txt", date(2026, 8, 20)) == []


def test_october_list_letter_spaced_labels_and_larger_page_units():
    # `_runs` of "081026 Mazda Price List.pdf", read through the shared browser on 2026-10-17 (robots override).
    rows = _rows("mazda_081026.txt", date(2026, 10, 17))
    got = {(r.model, r.variant): (r.list_price, r.price, r.fuel) for r in rows}
    assert len(rows) == 11
    assert got[("Mazda 2", "1.5L Hatchback Sports")] == (237888, 194888, "")
    assert got[("Mazda 3 Sedan M-Hybrid", "1.5L Sedan Classic Plus")] == (249888, 206888, "hybrid")   # price 7 units off the row
    assert got[("Mazda 3 Hatchback M-Hybrid", "1.5L Hatchback Astina")] == (264888, 221888, "hybrid")
    assert got[("Mazda CX-60", "2.5L Luxury")] == (370888, 322888, "")
    assert got[("Mazda CX-80 PHEV", "2.5L Luxury Sports (7 seater)")] == (410888, 362888, "hybrid")
    assert got[("Mazda 6e Fastback", "Sports")] == (277888, 229888, "ev")
    assert got[("Mazda 6e Fastback", "GT")] == (283888, 235888, "ev")
    assert all(r.includes_coe == "with" and "valid 08-Oct-2026 to 21-Oct-2026" in r.note for r in rows)
    assert _rows("mazda_081026.txt", date(2026, 10, 22)) == []


def test_unspace():
    assert mazda._unspace("M A Z D A  3  H A T C H B A C K / M - H Y B R I D") == "MAZDA 3 HATCHBACK M-HYBRID"
    assert mazda._unspace("M A Z D A 6 e  F A S T B A C K") == "MAZDA 6e FASTBACK"
    assert mazda._unspace("1.5L Sedan Luxury") == "1.5L Sedan Luxury"


def test_missing_pdf_reads_as_empty_and_bytes_come_from_pdf_bytes():
    import httpx

    from scrapers.base import FetchError

    class S:
        _read_cache = staticmethod(lambda key: None)
        _write_cache = staticmethod(lambda key, text: None)

        @staticmethod
        def pdf_bytes(url):
            req = httpx.Request("GET", url)
            raise FetchError("404") from httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))

    assert mazda._pdf_runs(S(), "https://mazda.com.sg/pdf/gone.pdf") == ""
