"""Omoda / Jaecoo official prices, parsed offline from live copies saved on 2026-10-17."""
from dataclasses import asdict
from pathlib import Path

import official
from models import NewEvVariant
from scrapers.official.omoda_jaecoo import SITE, model_urls, parse

FIX = Path(__file__).parent.parent / "fixtures" / "official"
PAGES = {"omoda-e5": "/omoda/omoda-e5/", "j5": "/jaecoo/j5/", "j6": "/jaecoo/j6/", "j7": "/jaecoo/j7/",
         "j8": "/jaecoo/j8/"}


def _rows():
    rows = []
    for name, path in PAGES.items():
        rows += parse((FIX / f"omoda_jaecoo_{name}.html").read_text(encoding="utf-8"), SITE + path)
    return {(r.make, r.model): r for r in rows}, rows


def test_menu_links_every_model():
    urls = model_urls((FIX / "omoda_jaecoo_jaecoo.html").read_text(encoding="utf-8"))
    assert {SITE + p for p in PAGES.values()} | {SITE + "/omoda/omoda-dna/"} == set(urls)


def test_prices_match_the_pages():
    by_model, rows = _rows()
    assert len(rows) == 5
    seen = {(r.make, r.model, r.variant, r.price) for r in rows}
    # Model without the make and no variant name, the way Sgcarmart stores these one trim models.
    assert seen == {("Omoda", "E5", "", 174999), ("Jaecoo", "5", "", 183999), ("Jaecoo", "6", "", 184999),
                    ("Jaecoo", "7", "", 219999), ("Jaecoo", "8", "", 249999)}
    assert by_model["Jaecoo", "8"].packages == {"Non-Guaranteed COE": 249999, "Guaranteed COE": 258999}
    assert by_model["Omoda", "E5"].packages == {"Non-Guaranteed COE": 174999, "Guaranteed COE": 178999}
    assert all(r.includes_coe == "with" and r.list_price is None for r in rows)
    assert {m: r.fuel for (_, m), r in by_model.items()} == {
        "E5": "ev", "5": "ev", "6": "ev", "7": "hybrid", "8": "hybrid"}
    assert by_model["Omoda", "E5"].note == ("Cat A COE $130,001 on 7 October 2026. "
                                         "Promotion valid until 21st October 2026")


def test_rows_pair_with_sgcarmart_variants(cfg):
    """Sgcarmart's names from site/live/data.json. A naming mismatch (make inside the model) matches nothing."""
    _, rows = _rows()

    def ev(make, model, variant, price):
        return NewEvVariant(make=make, model=model, variant=variant, price_with_coe=price, listing_url="u",
                            price_source_url="u", source="t")

    j5, e5 = ev("Jaecoo", "5", "58.9 kWh(A)", 183999), ev("Omoda", "E5", "[99kW] 61 kWh(A)", 176999)
    j6 = [ev("Jaecoo", "6", "RWD 65.69 kWh(A)", 184999), ev("Jaecoo", "6", "AWD 65.69 kWh(A)", 199999)]
    found = official.match([j5, e5, *j6], [asdict(r) for r in rows], cfg)
    assert found[id(j5)]["price"] == 183999 and found[id(e5)]["price"] == 174999
    # one "from" price, two Sgcarmart trims: it prices the trim at that price (RWD), the AWD is 8 percent away and keeps Sgcarmart's
    assert found[id(j6[0])]["price"] == 184999 and id(j6[1]) not in found


def test_page_without_price_block_gives_nothing():
    assert parse("<html><title>Omoda DNA</title><body><p>Design</p></body></html>", SITE) == []
