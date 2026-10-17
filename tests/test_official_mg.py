from pathlib import Path
from types import SimpleNamespace

import official
from scrapers.official import mg

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "official"


def test_parse_mg_pricelist():
    rows = mg.parse((FIX / "mg_pricelist.txt").read_text(encoding="utf-8"))
    assert len(rows) == 5
    got = {(r.model, r.variant, r.price) for r in rows}
    # Checked by eye against "MG Pricelist Valid 20261008 - 20261021.pdf" (pypdf plain text): price is the cheapest package.
    assert got == {("S5", "Luxury", 188888), ("S6", "Luxury", 205888), ("4 Urban", "", 191888),
                   ("Cyberster", "RWD 250kW", 329888), ("Cyberster", "AWD 375kW", 348888)}
    by = {(r.model, r.variant): r for r in rows}
    assert by["S5", "Luxury"].list_price == 201888
    assert by["S5", "Luxury"].packages == {"Guaranteed COE": 191888, "Non Guaranteed COE": 188888}
    assert by["4 Urban", ""].list_price == 204888
    assert by["4 Urban", ""].packages == {"Guaranteed COE": 194888, "Non Guaranteed COE": 191888}
    assert by["Cyberster", "AWD 375kW"].list_price == 358888
    assert by["Cyberster", "AWD 375kW"].packages == {"Guaranteed COE": 348888}
    # The terms list the bid packages ("Guaranteed Package: 1 bid, $123,000 rebate"), so the prices include COE.
    assert all(r.make == "MG" and r.fuel == "ev" and r.includes_coe == "with" for r in rows)
    assert by["S5", "Luxury"].note.startswith("Valid from 08 Oct 2026 to 21 Oct 2026, 12pm. COE Cat A.")


def test_no_coe_wording_leaves_includes_coe_unknown():
    text = "A $201,888 MGS5 LUXURY  ($5,000)  ($4,000)  $191,888  $188,888 ($1,000)"
    assert [r.includes_coe for r in mg.parse(text)] == ["unknown"]


def test_rows_match_sgcarmart_names():
    """The website pairs official rows with Sgcarmart's names (site/live/data.json, 2026-10-17)."""
    rows = [r.as_row() for r in mg.parse((FIX / "mg_pricelist.txt").read_text(encoding="utf-8"))]
    cars = [SimpleNamespace(make="MG", model=m, variant=v, price_with_coe=p) for m, v, p in [
        ("S5", "Luxury 62kWh(A)", 188888), ("S6", "Luxury 77kWh(A)", 205888), ("4 Urban", "Luxury 54 kWh(A)", 191888),
        ("Cyberster", "77 kWh(A)", 329888), ("Cyberster", "AWD 77 kWh(A)", 348888)]]
    found = official.match(cars, rows, {})
    got = {(c.model, c.variant): (found[id(c)]["model"], found[id(c)]["variant"]) for c in cars if id(c) in found}
    # "RWD 250kW" shares no word with Sgcarmart's "77 kWh(A)"; it pairs on price, the only Cyberster row within 5%.
    assert got == {("S5", "Luxury 62kWh(A)"): ("S5", "Luxury"), ("S6", "Luxury 77kWh(A)"): ("S6", "Luxury"),
                   ("4 Urban", "Luxury 54 kWh(A)"): ("4 Urban", ""), ("Cyberster", "77 kWh(A)"): ("Cyberster", "RWD 250kW"),
                   ("Cyberster", "AWD 77 kWh(A)"): ("Cyberster", "AWD 375kW")}


def test_pricelist_link_found_on_home_page():
    url = mg.pricelist_url((FIX / "mg_home.html").read_text(encoding="utf-8"))
    assert url == "https://mg.com.sg/Upload/Pricelist/1/pdf/MG%20Pricelist%20Valid%2020261008%20-%2020261021.pdf"


def test_row_with_two_possible_names_is_left_out():
    text = "MGS5 LUXURY\n\nA $201,888 ($5,000) $191,888 $188,888\n\nMG4 URBAN\n"
    assert mg.parse(text) == []
