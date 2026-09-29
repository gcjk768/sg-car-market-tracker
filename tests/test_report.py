import re
from datetime import date

from models import ReportSection
from report import html_to_text, sample_report, trend_arrows, unavailable_section
from telegram_bot import MAX_MESSAGE_LENGTH


def test_sample_report_has_all_sections_in_order(cfg):
    sections = sample_report(cfg, date(2026, 9, 29))
    assert [s.key for s in sections] == cfg["telegram"]["section_order"]
    assert all(isinstance(s, ReportSection) for s in sections)


def test_every_sample_section_fits_one_message(cfg):
    for s in sample_report(cfg, date(2026, 9, 29)):
        assert len(s.html) <= MAX_MESSAGE_LENGTH, s.key


def test_pre_blocks_are_at_most_sixty_wide(cfg):
    for s in sample_report(cfg, date(2026, 9, 29)):
        for block in re.findall(r"<pre>(.*?)</pre>", s.html, flags=re.S):
            for line in block.split("\n"):
                assert len(line) <= cfg["telegram"]["table_width"], (s.key, line)


def test_link_numbers_match_table_rows(cfg):
    sections = {s.key: s for s in sample_report(cfg, date(2026, 9, 29))}
    for key in ("new_ev", "used_ev", "used_ice"):
        html = sections[key].html
        table_rows = []
        for block in re.findall(r"<pre>(.*?)</pre>", html, flags=re.S):
            lines = block.split("\n")
            table_rows += [l for l in lines if re.match(r"^\s*\d+ ", l)]
        links = re.findall(r'^(\d+)\. <a href="', html, flags=re.M)
        assert [int(n) for n in links] == list(range(1, len(table_rows) + 1)), key
        for line, n in zip(table_rows, links):
            assert line.lstrip().startswith(n), (key, line)


def test_new_ev_section_is_grouped_by_body_type(cfg):
    html = {s.key: s for s in sample_report(cfg, date(2026, 9, 29))}["new_ev"].html
    for group in ("Hatchback", "Sedan", "SUV", "MPV"):
        assert f"<pre>{group}\n" in html


def test_cost_table_links_match_car_columns(cfg):
    html = {s.key: s for s in sample_report(cfg, date(2026, 9, 29))}["costs"].html
    header = re.search(r"<pre>(.*?)\n", html, flags=re.S).group(1)
    links = re.findall(r'^(\d+)\. <a href="', html, flags=re.M)
    assert links == ["1", "2", "3"]
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
    for label in ("Deposit", "Loan", "Mth 7y loan", "Mth 5y loan"):
        assert label in costs_html
    assert "72,000 (40%)" in costs_html
    assert "deposit $72,000, $1,509/mth over 7y" in sections["new_ev"].html
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
