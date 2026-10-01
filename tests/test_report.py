import re
from datetime import date

from models import ReportSection
from report import html_to_text, sample_report, trend_arrows, unavailable_section
from telegram_bot import MAX_MESSAGE_LENGTH


def test_sample_report_has_all_sections_in_order(cfg):
    sections = sample_report(cfg, date(2026, 9, 29))
    assert [s.key for s in sections] == cfg["telegram"]["section_order"]
    assert all(isinstance(s, ReportSection) for s in sections)
    summary = sections[0].html
    assert "<b>Cheapest to own today</b> · <a href=" in summary and "best value" not in summary.lower()
    assert summary.startswith("🚗 <b>SG CAR MARKET DAILY</b> · Tue 29 Sep 2026")


def test_every_sample_section_fits_one_message(cfg):
    for s in sample_report(cfg, date(2026, 9, 29)):
        assert len(s.html) <= MAX_MESSAGE_LENGTH, s.key


def test_pre_blocks_are_at_most_sixty_wide(cfg):
    for s in sample_report(cfg, date(2026, 9, 29)):
        for block in re.findall(r"<pre>(.*?)</pre>", s.html, flags=re.S):
            for line in block.split("\n"):
                assert len(line) <= cfg["telegram"]["table_width"], (s.key, line)


def test_car_lists_are_numbered_cards_without_tables(cfg):
    sections = {s.key: s for s in sample_report(cfg, date(2026, 9, 29))}
    for key in ("new_ev", "used_ev", "used_ice"):
        html = sections[key].html
        assert "<pre>" not in html, key
        cards = re.findall(r'^\S+ <b>(\d+)\. <a href="http', html, flags=re.M)
        assert cards and [int(n) for n in cards] == list(range(1, len(cards) + 1)), key


def test_new_ev_section_is_grouped_by_body_type(cfg):
    html = {s.key: s for s in sample_report(cfg, date(2026, 9, 29))}["new_ev"].html
    for group in ("Hatchback", "Sedan", "SUV and crossover", "MPV"):
        assert f"<u>{group}</u>" in html


def test_cost_table_links_match_car_columns(cfg):
    html = {s.key: s for s in sample_report(cfg, date(2026, 9, 29))}["costs"].html
    header = re.search(r"<pre>(.*?)\n", html, flags=re.S).group(1)
    links = re.findall(r'^\S+ <b>(New EV|Used EV|Used ICE)</b> · <a href="', html, flags=re.M)
    assert links == ["New EV", "Used EV", "Used ICE"]
    for name in ("New EV", "Used EV", "Used ICE"):
        assert name in header


def test_trend_arrows():
    assert trend_arrows([1, 2, 2, 1, 3, 3]) == "▲•▼▲•"
    assert trend_arrows([5]) == "n/a"


def test_unavailable_section_is_flagged():
    s = unavailable_section("coe", "site changed layout")
    assert s.available is False
    assert "unavailable today" in s.html


def test_html_to_text_shows_links_and_unescapes():
    text = html_to_text('<b>Hi</b> &amp; <a href="https://x.y/z">go</a>')
    assert text == "Hi & go <https://x.y/z>"


def test_financing_rows_and_link_notes(cfg):
    sections = {s.key: s for s in sample_report(cfg, date(2026, 9, 29))}
    costs_html = sections["costs"].html
    for label in ("Deposit", "Loan", "Mth 7y", "Mth 5y"):
        assert label in costs_html
    assert re.search(r"Deposit +72,000", costs_html)
    # Price, deposit, monthly and depreciation sit on the car's own card.
    assert re.search(r"Tesla Model 3 RWD 110</a></b>.*\n💰 \$179,999 .*\n🏦 Deposit \$72,000 · \$1,509/mth over 7y\n📉 Dep \$[\d,]+/yr",
                     sections["new_ev"].html)
    assert "/mth over 7y" in sections["used_ev"].html
    for key in ("new_ev", "used_ev", "used_ice", "costs"):
        assert len(sections[key].html) <= MAX_MESSAGE_LENGTH
        for block in re.findall(r"<pre>(.*?)</pre>", sections[key].html, flags=re.S):
            for line in block.split("\n"):
                assert len(line) <= 60, line


def test_single_used_pick_keeps_its_own_column_name():
    from models import CostBreakdown, Drivetrain
    from report import costs_section

    b = CostBreakdown(label="Honda Vezel", drivetrain=Drivetrain.hybrid, road_tax=1, insurance_low=1,
                      insurance_high=2, depreciation=1, energy=1, fixed_extras=1)
    html = costs_section([(b, "https://x")], "", headers=["Used ICE"]).html
    assert "Used ICE" in html and "New EV" not in html
