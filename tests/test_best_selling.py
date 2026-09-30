import re
from datetime import date
from pathlib import Path

from models import NewEvVariant
from report import best_selling_ev_section, sample_report
from scrapers.new_ev import best_selling_by_body_type, make_matches, top_best_seller
from scrapers.registrations import body_code, parse_body_types_rows, parse_body_types_xlsx

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def test_body_codes():
    assert body_code("HB") == "Hatchback"
    assert body_code(" SDN ") == "Sedan"
    assert body_code("STW") == "Wagon"
    assert body_code("CPE/ Conv") == "Coupe"
    assert body_code("Total") is None
    assert body_code(12) is None


def test_spreadsheet_counts_only_electric_rows_per_body_type():
    counts, months = parse_body_types_xlsx((FIX / "lta_m03.xlsx").read_bytes())
    assert months == ["2026-01", "2026-02", "2026-07", "2026-08"]
    assert counts["SUV"]["BYD"] == 600 + 640 + 651 + 572
    assert counts["Hatchback"]["BYD"] == 210 + 190 + 149 + 1
    assert counts["MPV"]["BYD"] == 80 + 75 + 60 + 161
    assert counts["Sedan"]["TESLA"] == 180 + 170 + 150 + 160
    assert counts["SUV"]["ZEEKR"] == 250
    assert counts["Coupe"] == {"BMW": 2}
    # B.M.W. carries down to its petrol row, which is skipped; plug in hybrids are skipped too.
    assert counts["Sedan"]["BMW"] == 78
    assert "TOTAL" not in counts["SUV"]


def test_rows_without_make_or_fuel_headers_fall_back_to_positions():
    rows = [
        ("", "", "", "", "HB", "SDN", "SUV", "Total"),
        ("TESLA", "AMD", "Electric", 10, None, 4, 6, 10),
        ("TESLA", "AMD", "Petrol", 3, 3, None, None, 3),
    ]
    counts, _ = parse_body_types_rows([rows])
    assert counts == {"Sedan": {"TESLA": 4}, "SUV": {"TESLA": 6}}


def test_make_matching():
    assert make_matches("BYD", "BYD")
    assert make_matches("MERCEDES BENZ", "Mercedes-Benz")
    assert make_matches("BMW", "B.M.W.")
    assert make_matches("AION", "GAC Aion")
    assert not make_matches("MG", "Mercedes-Benz")
    assert not make_matches("TESLA", "BYD")


def _v(make, model, price, body, variant=""):
    return NewEvVariant(make=make, model=model, variant=variant, price_with_coe=price, range_km=400, body_type=body,
                        listing_url=f"https://x/{model}", price_source_url="u", source="t")


def test_ranking_per_body_type(cfg):
    cfg["new_ev"]["best_selling"] = {"brands_per_body_type": 2, "models_per_brand": 2}
    variants = [
        _v("BYD", "Atto 3", 171888, "SUV", "Dynamic"), _v("BYD", "Atto 3", 181888, "SUV", "Premium"),
        _v("BYD", "Atto 2", 151388, "SUV"), _v("BYD", "Sealion 7", 205388, "SUV"),
        _v("Tesla", "Model Y", 223127, "SUV"), _v("Tesla", "Model 3", 179999, "Sedan"),
        _v("GAC Aion", "UT", 148988, "Hatchback"),
    ]
    counts = {"SUV": {"BYD": 2463, "TESLA": 1220, "ZEEKR": 250}, "Sedan": {"TESLA": 660}, "Hatchback": {"AION": 185}, "Coupe": {"BMW": 0}}
    groups = dict(best_selling_by_body_type(variants, counts, cfg))
    assert list(groups) == ["Hatchback", "Sedan", "SUV"]  # config order, empty body types dropped
    suv = groups["SUV"]
    assert [e["make"] for e in suv] == ["BYD", "TESLA"]  # top two brands only
    # Cheapest variant per model, cheapest model first, at most two models.
    assert [(v.model, v.price_with_coe) for v in suv[0]["models"]] == [("Atto 2", 151388), ("Atto 3", 171888)]
    assert round(suv[0]["share"]) == round(2463 * 100 / (2463 + 1220 + 250))
    assert groups["Hatchback"][0]["models"][0].model == "UT"
    assert top_best_seller(list(groups.items())).model == "Atto 2"


def test_section_cards_and_missing_brands(cfg):
    groups = [("SUV", [
        {"make": "BYD", "registrations": 2463, "share": 62.3, "rank": 1, "models": [_v("BYD", "Atto 3", 171888, "SUV")]},
        {"make": "ZEEKR", "registrations": 250, "share": 6.3, "rank": 2, "models": []},
    ])]
    html = best_selling_ev_section(groups, ["2026-01", "2026-08"], cfg=cfg, source_url="https://lta/m03.xlsx").html
    assert html.startswith("<b>Best Selling Top EV</b>")
    assert "<u>SUV and crossover</u>" in html
    assert re.search(r'<b>1\. <a href="https://x/Atto 3">BYD Atto 3</a></b>\n\$171,888', html)
    assert "<i>#1 EV SUV brand · 2,463 registered · 62%</i>" in html
    assert "no model on today's price list: #2 Zeekr 250" in html
    assert "from Jan to Aug 2026" in html
    assert "Best Value" not in html
    assert "/km" not in html.replace(" km", "")  # no value score on the best selling cards


def test_sample_report_uses_best_selling_list(cfg):
    html = {s.key: s for s in sample_report(cfg, date(2026, 9, 29))}["new_ev"].html
    assert "Best Selling Top EV" in html and "Best Value" not in html
    assert "EV SUV brand" in html and "Zeekr" in html
