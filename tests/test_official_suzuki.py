from pathlib import Path

from scrapers.official import suzuki

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "official"
BASE = "https://www.suzukicar.com.sg/vehicles/suzuki-singapore/"
SLUGS = ["e-vitara", "fronx", "jimny", "jimny-5-door", "s-cross", "swift"]


def _rows():
    menu = (FIX / "suzuki_home.html").read_text(encoding="utf-8")
    pages = {f"{BASE}{s}/": (FIX / f"suzuki_{s}.html").read_text(encoding="utf-8") for s in SLUGS}
    return suzuki.parse(menu, pages)


def test_parse_fixture():
    rows = _rows()
    assert len(rows) == 6
    got = {(r.model, r.variant, r.price, r.fuel) for r in rows}
    assert {("e VITARA", "", 179888, "ev"), ("Swift", "", 169888, "hybrid"), ("Fronx", "", 199888, "hybrid"),
            ("S-Cross", "", 209888, "hybrid"), ("Jimny", "", 186888, ""), ("Jimny 5-Door", "", 217888, "")} == got
    for r in rows:
        assert r.make == "Suzuki" and r.includes_coe == "unknown" and not r.packages and r.list_price is None
        assert r.source_url.startswith(BASE)


def test_missing_page_is_left_out():
    menu = (FIX / "suzuki_home.html").read_text(encoding="utf-8")
    assert suzuki.parse(menu, {}) == []
