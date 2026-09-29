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
        block = re.search(r"<pre>(.*?)</pre>", html, flags=re.S).group(1)
        table_rows = [l for l in block.split("\n")[2:] if l.strip()]
        links = re.findall(r'^(\d+)\. <a href="', html, flags=re.M)
        assert [int(n) for n in links] == list(range(1, len(table_rows) + 1)), key
        for line, n in zip(table_rows, links):
            assert line.lstrip().startswith(n), (key, line)


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
