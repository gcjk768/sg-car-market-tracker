"""BYD official price list, parsed from a saved copy of the 8 to 21 October 2026 PDF (layout text)."""
from pathlib import Path

from scrapers.official import byd

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "official"


def test_parse_price_list():
    rows = byd.parse((FIX / "byd_price_list_8-Oct-21-Oct.txt").read_text(encoding="utf-8"), "u")
    assert len(rows) == 17
    got = {(r.model, r.variant): r for r in rows}
    # (model, variant, price), checked by eye against the rendered PDF
    for model, variant, price in [
        ("Atto 1", "Cat A, 15.6 kWh/100km", 161888),
        ("M6", "Cat A, 18.7 kWh/100km", 182888),
        ("Seal", "2025, Cat B, 16.6 kWh/100km", 214888),
        ("Sealion 7 Extended Range", "Cat B, 21.9 kWh/100km", 248388),
        ("Seal 6 DM-i", "Dynamic, Cat B, 3.6 L/100km", 188888),
        ("Seal 6 DM-i", "Premium, Cat B, 3.9 L/100km", 196888),
        ("Sealion 8 DM-i", "Cat B, 5.3 L/100km", 231388),
    ]:
        assert got[(model, variant)].price == price
    atto1 = got[("Atto 1", "Cat A, 15.6 kWh/100km")]
    assert atto1.list_price == 236888
    assert atto1.packages == {"Savers": 161888, "Base": 166888, "Excite": 169888}
    assert got[("Sealion 7", "Cat B, 21.4 kWh/100km")].packages == {"Savers": 210888, "Base": 215888, "Excite": 218888}
    assert {r.includes_coe for r in rows} == {"with"}
    assert got[("Sealion 6 DM-i", "Cat B, 5.5 L/100km")].fuel == "hybrid"
    assert atto1.fuel == "ev"
    assert atto1.note.startswith("Valid from 8 October to 21 October 2026, 12PM")
    assert all(r.make == "BYD" and r.source_url == "u" for r in rows)


PDF = "https://bydcars.sg/wp-content/uploads/2026/10/BYD-Price-List_8-Oct-21-Oct.pdf"


def test_latest_pdf_from_media_index():
    # byd_media.json is the answer to byd.MEDIA_URL itself (curl, 17 October 2026): price lists mixed with spec sheets
    media = (FIX / "byd_media.json").read_text(encoding="utf-8")
    assert "Specification-Sheet" in media
    assert byd.latest_pdf(media) == PDF
    # the file name filter in latest_pdf chooses the price list, not the site's search behaviour
    assert "search=" not in byd.MEDIA_URL


def test_scrape_reads_media_index_then_pdf():
    class Fake:
        def page(self, url):
            assert url == byd.MEDIA_URL
            return (FIX / "byd_media.json").read_text(encoding="utf-8")

        def pdf(self, url):
            assert url == PDF
            return (FIX / "byd_price_list_8-Oct-21-Oct.txt").read_text(encoding="utf-8")

    rows = byd.scrape(Fake())
    assert len(rows) == 17 and {r.source_url for r in rows} == {PDF}
