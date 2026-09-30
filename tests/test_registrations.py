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
