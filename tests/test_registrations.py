from report import top_sellers_section
from scrapers.registrations import parse_registrations

# Lines as pypdf extracts them from LTA table M03, both half year page groups.
TEXT = """NEW REGISTRATION OF CARS BY MAKE IN 2026 2nd HALF 1
B.M.W. AMD Petrol 225 27 27 47 101 44 39 40 1 124
B.M.W. AMD Electric 127 24 2 36 62 24 1 40 65
BYD AMD Petrol-Electric (Plug-In) 348 16 2 87 105 16 9 218 243
BYD AMD Electric 1852 149 195 651 995 1 123 161 572 857
Total Total Total 9502 195 694
2026-08
2026-07
NEW REGISTRATION OF CARS BY MAKE IN 2026 1st HALF 1
BYD AMD Electric 1000 1 2 3
MERCEDES BENZ PI Petrol-Electric 2 2 2
2026-022026-01 2026-03
"""


def test_parse_sums_halves_by_make():
    makes, months = parse_registrations(TEXT)
    assert makes["BYD"] == {"total": 3200, "ev": 2852, "petrol": 348}
    assert makes["BMW"] == {"total": 352, "ev": 127, "petrol": 225}
    assert makes["MERCEDES BENZ"]["total"] == 2
    assert "Total" not in makes
    assert months == ["2026-01", "2026-02", "2026-03", "2026-07", "2026-08"]


def test_section_ranks_by_registrations():
    makes, months = parse_registrations(TEXT)
    html = top_sellers_section(makes, months, top_n=2).html
    ev, petrol = html.split("Top petrol brands")
    assert ev.index("BYD") < ev.index("BMW")
    assert petrol.index("BYD") < petrol.index("BMW")
    assert "Mercedes" not in html
    assert "from Jan to Aug 2026" in html


def test_ev_list_can_be_longer_than_petrol():
    makes = {f"Make{i}": {"total": 100 - i, "ev": 100 - i, "petrol": 100 - i} for i in range(60)}
    ev, petrol = top_sellers_section(makes, [], top_n=20, top_n_ev=50).html.split("Top petrol brands")
    assert "50. " in ev and "51. " not in ev
    assert "20. " in petrol and "21. " not in petrol


def test_ev_list_names_each_brands_best_seller():
    makes = {"BYD": {"total": 900, "ev": 800, "petrol": 100}, "TESLA": {"total": 300, "ev": 300, "petrol": 0},
             "OPEL": {"total": 10, "ev": 10, "petrol": 0}}
    html = top_sellers_section(makes, [], ev_models={"BYD": "Atto 3", "TESLA": "Model Y"}, models_checked_on="2026-10-01").html
    ev = html.split("Top petrol brands")[0]
    assert "Top EV brands" not in html and "1,110 cars" not in html
    assert "<b>BYD</b> Atto 3" in ev and "<b>Tesla</b> Model Y" in ev
    assert "3. <b>Opel</b>  10" in ev  # no model known, brand alone
    assert "checked 2026-10-01" in html

def test_ev_list_has_no_cap_by_default_and_shows_price():
    makes = {f"Make{i}": {"total": 100 - i, "ev": 100 - i, "petrol": 0} for i in range(60)}
    makes["BYD"] = {"total": 900, "ev": 800, "petrol": 0}
    html = top_sellers_section(makes, [], top_n=20, ev_models={"BYD": "Atto 3"}, ev_prices={"BYD": 152888}).html
    assert "61. " in html
    assert "<b>BYD</b> Atto 3  from $152,888" in html
    assert "cheapest variant on today's price list" in html


def test_model_prices_takes_cheapest_whole_word_match():
    from models import NewEvVariant
    from scrapers.new_ev import model_prices

    def v(make, model, variant, price):
        return NewEvVariant(make=make, model=model, variant=variant, price_with_coe=price,
                            listing_url="u", price_source_url="u", source="t")
    variants = [v("BYD", "Atto 3", "Extended", 160000), v("BYD", "Atto 3", "Standard", 150000),
                v("BYD", "Seal", "", 100000), v("Polestar", "4", "Long Range 2024", 200000),
                v("Mercedes-Benz", "CLA 250+", "", 210000)]
    got = model_prices(variants, {"BYD": "Atto 3", "POLESTAR": "2", "MERCEDES BENZ": "CLA", "TESLA": "Model Y"})
    assert got == {"BYD": 150000, "MERCEDES BENZ": 210000}
