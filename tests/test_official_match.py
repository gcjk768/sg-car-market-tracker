"""Official prices onto Sgcarmart variants (official.py)."""
from datetime import date

import official
from db import Database
from models import NewEvVariant


def ev(model, variant, price, make="BYD"):
    return NewEvVariant(make=make, model=model, variant=variant, price_with_coe=price, listing_url="u", price_source_url="u", source="t")


def row(model, variant, price, make="BYD", coe="with", **kw):
    return {"make": make, "model": model, "variant": variant, "price": price, "includes_coe": coe, **kw}


def test_variant_names_are_matched_by_their_words(cfg):
    dyn, prem = ev("Sealion 7", "Dynamic 71.8kWh(A)", 202388), ev("Sealion 7", "Premium 82.5 kWh(A)", 215888)
    rows = [row("Sealion 7", "Dynamic", 199388, packages={"Savers": 199388, "Base": 204388}, list_price=274388),
            row("Sealion 7", "Premium", 213388)]
    info = official.apply([dyn, prem], rows, cfg)
    assert dyn.price_with_coe == 199388 and prem.price_with_coe == 213388
    fields = official.card_fields(info[id(dyn)])
    assert fields["price_source"] == "official" and fields["sgcarmart_price"] == 202388 and fields["list_price"] == 274388


def test_no_match_keeps_sgcarmart(cfg):
    seal = ev("Seal", "Dynamic 82.56 kWh(A)", 196888)
    atto = ev("Atto 3", "Evo 60 kWh", 184888)
    rows = [row("Sealion 7", "Dynamic", 199388),                 # another model: "Seal" is not "Sealion 7"
            row("Atto 3", "Evo", 274888),                       # list price far above: not the same package
            row("Seal", "Dynamic", 150000, coe="without")]      # priced without COE: never used
    assert official.apply([seal, atto], rows, cfg) == {}
    assert seal.price_with_coe == 196888 and atto.price_with_coe == 184888
    assert official.card_fields(None) == {"price_source": "sgcarmart"}


def test_an_ambiguous_variant_stays_unmatched(cfg):
    one = ev("EV5", "Electric 64 kWh", 210000, make="Kia")
    rows = [row("EV5", "Air", 205000, make="Kia"), row("EV5", "Earth", 215000, make="Kia")]
    assert official.apply([one], rows, cfg) == {}               # neither variant word appears: no guess


def test_official_rows_keep_each_brands_last_good_list(tmp_path):
    db = Database(tmp_path / "c.db")
    db.upsert_official([row("Sealion 7", "Dynamic", 199388)], date(2026, 10, 16), {"BYD"})
    db.upsert_official([row("EV5", "Air", 205000, make="Kia")], date.today(), {"Kia"})   # BYD failed today
    got = {(r["make"], r["model"]) for r in db.latest_official(max_age_days=3650)}
    assert got == {("BYD", "Sealion 7"), ("Kia", "EV5")}
    db.upsert_official([row("EV5", "Earth", 215000, make="Kia")], date.today(), {"Kia"})  # a re-run replaces the brand
    assert [r["variant"] for r in db.latest_official(3650) if r["make"] == "Kia"] == ["Earth"]


def test_only_the_closest_family_competes(cfg):
    # "Octavia" and "Octavia Combi" share variant names; each Sgcarmart family must take its own, not tie between them
    sedan, wagon = ev("Octavia Mild Hybrid", "1.5 TSI Selection", 184900, "Skoda"), ev("Octavia Combi Mild Hybrid", "1.5 TSI Selection", 199900, "Skoda")
    wagon_rs = ev("Octavia Combi RS", "2.0 TSI", 250900, "Skoda")
    rows = [row("Octavia", "Selection 1.5 TSI", 184000, "Skoda"), row("Octavia", "RS 2.0 TSI", 229900, "Skoda"),
            row("Octavia Combi", "Selection 1.5 TSI", 199000, "Skoda"), row("Octavia Combi", "RS 2.0 TSI", 250000, "Skoda")]
    official.apply([sedan, wagon, wagon_rs], rows, cfg)
    assert (sedan.price_with_coe, wagon.price_with_coe, wagon_rs.price_with_coe) == (184000, 199000, 250000)


def test_one_official_row_prices_one_variant(cfg):
    # seen live 17 Oct: Nissan lists one Serena price; Sgcarmart has three Serenas within 5 percent of it
    v7, welcab, smart8 = (ev("Serena e-POWER Hybrid", n, p, "Nissan") for n, p in
                          (("1.4 Highway Star V 7-Seater", 220800), ("1.4 XV Welcab Lift 6-Seater", 225200), ("Highway Star Smart 8", 234800)))
    official.apply([v7, welcab, smart8], [row("Serena e-POWER", "Serena e-POWER Highway Star Prestige Smart 8", 231800, "Nissan")], cfg)
    assert (v7.price_with_coe, welcab.price_with_coe, smart8.price_with_coe) == (220800, 225200, 231800)


def test_byd_rows_match_by_coe_category(cfg):
    from models import CoeCategory
    a, b = ev("Sealion 7", "Dynamic 71.8kWh", 202388), ev("Sealion 7", "Premium 82.5 kWh", 215888)
    a.coe_category, b.coe_category = CoeCategory.A, CoeCategory.B
    official.apply([a, b], [row("Sealion 7", "Cat A, 18.8 kWh/100km", 197388), row("Sealion 7", "Cat B, 21.4 kWh/100km", 210888)], cfg)
    assert (a.price_with_coe, b.price_with_coe) == (197388, 210888)


def test_a_price_without_coe_takes_todays_premium(cfg):
    from models import NewCarVariant
    cayenne = NewCarVariant(make="Porsche", model="Cayenne", variant="3.0 V6", fuel="petrol", price_with_coe=595000,
                            power_kw=260, listing_url="u", source="t")
    rows = [row("Cayenne", "Cayenne", 465000, "Porsche", coe="without")]
    info = official.apply([cayenne], rows, cfg, premiums={"A": 130001, "B": 130100}, cat_of=lambda v: "B")
    assert cayenne.price_with_coe == 465000 + 130100
    f = official.card_fields(info[id(cayenne)])
    assert (f["price_ex_coe"], f["coe_added"], f["coe_cat"]) == (465000, 130100, "B")
    other = NewCarVariant(make="Porsche", model="Cayenne", variant="x", fuel="petrol", price_with_coe=595000, listing_url="u", source="t")
    assert official.apply([other], rows, cfg) == {}          # no premium given: never used


def test_prices_listed_without_coe_take_the_premium_then_match(cfg):
    from models import NewCarVariant
    flagged = NewCarVariant(make="Porsche", model="Taycan Electric", variant="Turbo 105 kWh", fuel="ev", price_with_coe=759388,
                            price_includes_coe=False, power_kw=600, listing_url="u", source="t")
    ev_list = ev("Taycan", "Turbo 105 kWh(A)", 759388, "Porsche")          # same car on the EV list, no flag there
    ids = official.without_coe_ids([ev_list], [flagged])
    assert ids == {id(flagged), id(ev_list)}
    prem = {"A": 130001, "B": 130100}
    info = official.add_coe([ev_list, flagged], ids, prem, lambda v: "B")
    assert ev_list.price_with_coe == 759388 + 130100 and info[id(ev_list)]["coe_cat"] == "B"
    rows = [row("Taycan", "Taycan Turbo", 759388, "Porsche", coe="without"), row("Taycan", "Taycan 4S Black Edition", 608288, "Porsche", coe="without")]
    found = official.match([ev_list], rows, cfg, prem, lambda v: "B")
    assert found[id(ev_list)]["variant"] == "Taycan Turbo" and found[id(ev_list)]["price"] == 759388 + 130100


def test_pdf_plain_mode_really_reads_plain_and_caches_under_its_key(cfg, tmp_path, monkeypatch):
    from scrapers.official import OfficialScraper
    import scrapers.official as off
    cfg["general"]["cache_dir"] = str(tmp_path)
    s = OfficialScraper(cfg, "ua", date(2026, 10, 17))
    seen = []

    class Page:
        def extract_text(self, extraction_mode="plain"):
            seen.append(extraction_mode)
            return "" if extraction_mode == "layout" else "MG S5 $188,888"

    class Resp:
        status_code, content = 200, b"%PDF"
        def raise_for_status(self): pass

    monkeypatch.setattr("pypdf.PdfReader", lambda b: type("R", (), {"pages": [Page()]})())
    monkeypatch.setattr(s, "_allowed", lambda u: True)
    monkeypatch.setattr(s.client, "get", lambda u: Resp())
    monkeypatch.setattr(off.BaseScraper._throttle, "wait", lambda d: None)
    assert s.pdf("https://x/p.pdf", layout=False) == "MG S5 $188,888" and seen == ["plain"]
    assert s.pdf("https://x/p.pdf", layout=False) == "MG S5 $188,888" and seen == ["plain"]   # second call from the cache


def test_without_coe_card_hides_the_printed_list_and_packages():
    entry = {"official": {"price": 430099, "price_ex_coe": 299999, "coe_added": 130100, "coe_cat": "B", "list_price": 345999,
                          "packages": {"Package (without COE)": 299999}}, "sgcarmart_price": 430099}
    f = official.card_fields(entry)
    assert f["list_price"] is None and f["packages"] == {} and f["coe_added"] == 130100
