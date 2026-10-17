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


def test_model_prices_takes_cheapest_whole_word_match():
    from models import NewEvVariant
    from scrapers.new_ev import model_prices

    def v(make, model, variant, price):
        return NewEvVariant(make=make, model=model, variant=variant, price_with_coe=price,
                            listing_url="u", price_source_url="u", source="t")
    variants = [v("BYD", "Atto 3", "Extended", 160000), v("BYD", "Atto 3", "Standard", 150000),
                v("BYD", "Seal 6", "Premium", 90000), v("BYD", "Seal", "", 100000),
                v("Polestar", "4", "Long Range 2024", 200000),
                v("Mercedes-Benz", "CLA 250+", "", 210000), v("Aion", "V", "Luxury", 170000)]
    got = model_prices(variants, {"BYD": ["Atto 3", "Seal", "Seal 6"], "POLESTAR": "2", "MERCEDES BENZ": "CLA",
                                  "TESLA": "Model Y", "GAC": "Aion V"})
    assert got == {"BYD": {"Atto 3": 150000, "Seal": 100000, "Seal 6": 90000},  # Seal 6 is not priced as Seal
                   "MERCEDES BENZ": {"CLA": 210000}, "GAC": {"Aion V": 170000}}
