from pathlib import Path

from scrapers.official.bmw import find_pricelist, parse, scrape

URL = "https://www.bmw.com.sg/content/dam/bmw/marketASIA/bmw_com_sg/pdfs/pricelist/2026.10.07-BMW-SG-Pricelist.pdf.asset.1791518795005.pdf"
FIX = Path(__file__).parent.parent / "fixtures" / "official"
TEXT = (FIX / "bmw_pricelist.txt").read_text(encoding="utf-8")


def test_parse_price_list():
    rows = parse(TEXT, URL)
    assert len(rows) == 52
    got = {(r.model, r.variant): r for r in rows}
    for model, variant, price in [
        ("1 Series", "116 M Sport", 260888),
        ("3 Series Sedan", "318i M Sport", 331888),
        ("5 Series Sedan", "520i", 388888),
        ("2 Series Gran Coupe", "216 M Sport", 269888),
        ("X7", "X7 M60i xDrive 6-Seater", 863888),
        ("iX1", "iX1 eDrive20 xLine", 313888),
        ("i5 Sedan", "i5 M60 xDrive", 562888),
        ("M5 Touring", "M5", 804888),
        ("1 Series", "M135 xDrive", 372888),
    ]:
        assert got[(model, variant)].price == price, (model, variant)
    assert all(r.make == "BMW" and r.includes_coe == "with" and not r.packages for r in rows)
    assert all(r.note == "Price list valid from 7th October 2026" and r.source_url == URL for r in rows)
    assert got[("iX1", "iX1 eDrive20 xLine")].fuel == "ev"
    assert got[("i4 Gran Coupe", "i4 M60 xDrive")].fuel == "ev"
    assert got[("M5 Sedan", "M5")].fuel == "hybrid"
    assert got[("X7", "X7 M60i xDrive 6-Seater")].fuel == ""
    assert got[("5 Series Sedan", "520i")].fuel == ""
    assert sum(r.fuel == "ev" for r in rows) == 14


def test_find_pricelist():
    html = ('<a href="/content/dam/x/pdfs/pricelist/2026.09.01-BMW-SG-Pricelist.pdf.asset.1.pdf">old</a>'
            '<a href="/content/dam/x/pdfs/pricelist/2026.10.07-BMW-SG-Pricelist.pdf.asset.2.pdf">new</a>')
    assert find_pricelist(html) == "https://www.bmw.com.sg/content/dam/x/pdfs/pricelist/2026.10.07-BMW-SG-Pricelist.pdf.asset.2.pdf"
    assert find_pricelist("<p>none</p>") is None


def test_find_pricelist_on_saved_e_brochures_page():
    # Plain curl copy of the e-brochures page (2026-10-17): what s.page() returns.
    assert find_pricelist((FIX / "bmw_e-brochures.html").read_text(encoding="utf-8")) == URL


class _Fake:
    def __init__(self, html, md=""):
        self.html, self.md, self.read = html, md, []

    def page(self, url):
        return self.html

    def rendered(self, url):
        self.read.append(url)
        return self.md

    def pdf(self, url):
        assert url == URL
        return TEXT


def test_scrape_uses_plain_page_then_browser_copy():
    s = _Fake((FIX / "bmw_e-brochures.html").read_text(encoding="utf-8"))
    assert len(scrape(s)) == 52 and not s.read          # the plain page is enough, no browser
    s = _Fake("<div data-src='script'></div>", f'<a href="{URL}">BMW Price List</a>')
    assert len(scrape(s)) == 52 and s.read               # link only once the page is drawn
