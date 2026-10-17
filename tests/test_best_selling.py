from pathlib import Path

from models import NewEvVariant
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


def test_live_header_with_make_and_fuel_in_one_cell():
    # As LTA's DataMall spreadsheet has it since 2026-09.
    rows = [
        ("Period (Month)", None, None, "Total", "2026-07", None, None, None, None, None, "Total"),
        ("Make/ Importer Type 3/ Fuel Type/ Car Type 2", None, None, None, "HB", "SDN", "MPV", "STW", "SUV", "CPE/ Conv", "Total"),
        ("AUDI", "AMD", "Petrol", 10, None, 7, None, None, None, None, 7),
        ("AUDI", "AMD", "Electric", 17, None, None, None, None, 10, None, 10),
    ]
    counts, months = parse_body_types_rows([rows])
    assert counts == {"SUV": {"AUDI": 10}}
    assert months == ["2026-07"]


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
