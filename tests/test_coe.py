from datetime import date
from pathlib import Path

import pytest

from models import CoeCategory
from scrapers.coe import (
    SgcarmartCoeScraper,
    OneMotoringCoeScraper,
    exercise_label,
    find_tender_date,
    next_tender_date,
    parse_coe_tables,
    tender_result_dates,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def test_parse_rows_orientation():
    data = parse_coe_tables((FIX / "onemotoring_coe.html").read_text())
    assert data["A"] == {"quota": 1193, "bids": 1507, "successful": 1191, "premium": 131890}
    assert data["E"]["premium"] == 137000
    assert set(data) == set("ABCDE")


def test_parse_columns_orientation():
    data = parse_coe_tables((FIX / "sgcarmart_coe.html").read_text())
    assert data["B"]["premium"] == 133000
    assert data["C"]["quota"] == 318
    assert data["D"]["successful"] == 494


def test_tender_date_from_fixtures():
    today = date(2026, 9, 29)
    assert find_tender_date((FIX / "onemotoring_coe.html").read_text(), today) == date(2026, 9, 23)
    assert find_tender_date((FIX / "sgcarmart_coe.html").read_text(), today) == date(2026, 9, 23)


def test_scraper_parse_builds_models(cfg):
    s = OneMotoringCoeScraper(cfg, "test-agent", date(2026, 9, 29))
    results = s.parse((FIX / "onemotoring_coe.html").read_text())
    assert len(results) == 5
    a = next(r for r in results if r.category == CoeCategory.A)
    assert a.quota_premium == 131890 and a.tender_date == date(2026, 9, 23)
    assert a.exercise == "September 2026 second exercise"
    s2 = SgcarmartCoeScraper(cfg, "test-agent", date(2026, 9, 29))
    assert len(s2.parse((FIX / "sgcarmart_coe.html").read_text())) == 5


def test_exercise_label():
    assert exercise_label(date(2026, 9, 9)) == "September 2026 first exercise"
    assert exercise_label(date(2026, 9, 23)) == "September 2026 second exercise"


def test_tender_result_dates_september_2026():
    # First Monday of September 2026 is the 7th, third Monday is the 21st.
    assert tender_result_dates(2026, 9) == [date(2026, 9, 9), date(2026, 9, 23)]


@pytest.mark.parametrize(
    "after, expected",
    [
        (date(2026, 9, 23), date(2026, 10, 7)),   # result day itself rolls to next tender
        (date(2026, 9, 29), date(2026, 10, 7)),
        (date(2026, 10, 7), date(2026, 10, 21)),
        (date(2026, 12, 20), date(2026, 12, 23)),
        (date(2026, 12, 24), date(2027, 1, 6)),   # crosses the year
    ],
)
def test_next_tender_date(after, expected):
    assert next_tender_date(after) == expected


def test_motorist_live_page_date_and_previous_tender():
    # Saved 2026-09-29. The header "Results Sep 26 September 2026" used to read as 26 September.
    from datetime import date
    from pathlib import Path

    from scrapers.coe import MotoristCoeScraper
    from settings import load_config

    html = (Path(__file__).resolve().parent.parent / "fixtures" / "motorist_coe_live.html").read_text(encoding="utf-8")
    rows = MotoristCoeScraper(load_config(), "ua", date(2026, 9, 29)).parse(html)
    by = {(r.tender_date, r.category.value): r for r in rows}
    assert by[(date(2026, 9, 23), "A")].quota_premium == 131890
    assert by[(date(2026, 9, 9), "A")].quota_premium == 133009


def test_find_tender_date_prefers_motorist_heading():
    from datetime import date
    from scrapers.coe import find_tender_date
    html = "<body>COE Prices 23/09/2026 Quota Premium $1 <p>Updated 7 Oct 2026 bidding news</p></body>"
    assert find_tender_date(html, date(2026, 10, 7)) == date(2026, 9, 23)


def test_datagov_history_maps_exercises_to_result_days(cfg, tmp_path, monkeypatch):
    from scrapers.coe import DataGovCoeScraper

    cfg["general"]["cache_dir"] = str(tmp_path)
    s = DataGovCoeScraper(cfg, "test", date(2026, 10, 8))
    monkeypatch.setattr(s, "fetch", lambda url, params=None: (FIX / "datagov_coe.json").read_text())
    rows = s.history(2)
    assert {r.tender_date for r in rows} == {date(2026, 10, 7), date(2026, 9, 23)}   # 2026-10 round 1, 2026-09 round 2
    a = next(r for r in rows if r.category == CoeCategory.A and r.tender_date == date(2026, 10, 7))
    assert (a.quota_premium, a.quota, a.bids_received) == (130001, 1232, 1560)
    assert {r.tender_date for r in s.run()} == {date(2026, 10, 7)}
    s.close()


def test_schedule_pdf_text_fixes_holiday_weeks():
    from scrapers.coe import SCHEDULE, parse_schedule, tender_result_dates

    text = (FIX / "coe_schedule_2026.txt").read_text()
    sched = parse_schedule(text)
    assert len(sched) == 12 and sum(len(v) for v in sched.values()) == 24
    assert sched[(2026, 2)][1] == (__import__("datetime").datetime(2026, 2, 16, 12), __import__("datetime").datetime(2026, 2, 20, 16))  # CNY week
    SCHEDULE.update(sched)
    try:
        assert tender_result_dates(2026, 6) == [date(2026, 6, 4), date(2026, 6, 17)]    # Vesak Monday: Tue to Thu
        assert next_tender_date(date(2026, 10, 7)) == date(2026, 10, 21)
    finally:
        SCHEDULE.clear()


def test_news_feed_parse_filter_and_first_read_is_capped(cfg):
    from news_watch import FIRST_READ_CAP, new_items
    from scrapers.lta_news import parse_feed, relevant

    items = parse_feed((FIX / "lta_news_feed.xml").read_text())
    assert len(items) >= 10 and all(i.link.startswith("https://") and i.published[:2] == "20" for i in items)
    kw = cfg["news"]["keywords"]
    first, seen = new_items(items, None, kw)                                     # first read: newest relevant items, capped
    assert seen == {i.link for i in items}
    assert first == sorted([i for i in items if relevant(i, kw)], key=lambda i: i.published, reverse=True)[:FIRST_READ_CAP]
    assert len(new_items(items, None, ["the"], first_cap=2)[0]) == 2
    fresh, seen = new_items(items, {i.link for i in items[1:]}, ["deregistered"])
    assert [i.link for i in fresh] == [items[0].link]                            # one unseen item, and it matches
    assert new_items(items, seen, ["deregistered"])[0] == []                     # sent once
    assert new_items(items, {i.link for i in items[1:]}, ["zzz"])[0] == []       # unseen but not relevant


def test_lta_live_page_table_with_unlabelled_description_column():
    html = """<table><tr><th>Category</th><th>Quota</th><th>QP($)</th></tr>
    <tr><td>A</td><td>CAR-DETAILS AT ONEMOTORING</td><td>1,232</td><td>130,001</td></tr>
    <tr><td>B</td><td>CAR-DETAILS AT ONEMOTORING</td><td>945</td><td>130,100</td></tr></table>"""
    assert parse_coe_tables(html) == {"A": {"quota": 1232, "premium": 130001}, "B": {"quota": 945, "premium": 130100}}


def test_news_seen_list_keeps_every_link_in_a_long_feed(tmp_path, cfg, monkeypatch):
    import json
    import news_watch
    from db import Database
    from scrapers.lta_news import NewsItem

    items = [NewsItem(f"coe item {n}", f"https://x/{n:04d}", "2026-10-01", "") for n in range(600)]
    cfg["general"]["db_path"] = str(tmp_path / "cars.db")
    monkeypatch.setattr(news_watch, "load_config", lambda: cfg)
    cfg["sources"]["media_news_feeds"] = {}
    monkeypatch.setattr(news_watch.LtaNewsScraper, "run", lambda self, url=None: items)
    news_watch.main([])                                  # first run records everything
    db = Database(cfg["general"]["db_path"])
    assert set(json.loads(db.get_state(news_watch.SEEN_KEY))) == {i.link for i in items}
    db.close()
    assert news_watch.new_items(items, {i.link for i in items}, ["coe"])[0] == []


def test_news_watch_stores_new_items_newest_first_and_the_site_shows_them(tmp_path, cfg, monkeypatch):
    import json
    import news_watch
    import website
    from db import Database
    from scrapers.lta_news import NewsItem

    old = [NewsItem(f"COE quota {n}", f"https://x/old{n}", f"2026-09-{n + 10:02d}", "s") for n in range(3)]
    cfg["general"]["db_path"] = str(tmp_path / "cars.db")
    cfg["news"]["keywords"] = ["coe"]
    feed = list(old)
    monkeypatch.setattr(news_watch, "load_config", lambda: cfg)
    cfg["sources"]["media_news_feeds"] = {}
    monkeypatch.setattr(news_watch.LtaNewsScraper, "run", lambda self, url=None: feed)
    news_watch.main([])                                  # first run: the list starts with what is relevant in the feed
    db = Database(cfg["general"]["db_path"])
    assert [r["link"] for r in json.loads(db.get_state(news_watch.NEWS_KEY))] == ["https://x/old2", "https://x/old1", "https://x/old0"]
    feed[:0] = [NewsItem("Road works", "https://x/other", "2026-10-02", ""),
                NewsItem("COE results", "https://x/new", "2026-10-01", "Premiums fell")]
    news_watch.main([])
    news_watch.main([])                                  # seen links are never added twice
    stored = json.loads(db.get_state(news_watch.NEWS_KEY))
    assert stored[0] == {"title": "COE results", "link": "https://x/new", "published": "2026-10-01", "summary": "Premiums fell", "source": "LTA"}
    assert len(stored) == 4                              # "Road works" is not relevant
    assert news_watch.merge_news(stored, [NewsItem("COE", f"https://x/{n}", "2026-10-03", "") for n in range(40)])[0]["link"] == "https://x/0"
    assert len(news_watch.merge_news(stored, [NewsItem("COE", f"https://x/{n}", "2026-10-03", "") for n in range(40)])) == news_watch.NEWS_CAP

    monkeypatch.setattr(website, "scrape_registrations", lambda *a, **k: None)
    monkeypatch.setattr(website, "scrape_body_types", lambda *a, **k: None)
    monkeypatch.setattr(website, "scrape_motorbikes", lambda *a, **k: {"used": {}, "brands": None})
    assert website.build(cfg, db, date(2026, 10, 3), "ua")["news"] == stored
    db.close()


def test_straits_times_feed_parses_and_filters_with_the_news_keywords():
    from scrapers.lta_news import parse_feed, relevant

    items = parse_feed((FIX / "st_singapore_feed.xml").read_text(encoding="utf-8"))
    assert len(items) == 5 and all(i.link.startswith("https://www.straitstimes.com/singapore/") for i in items)
    assert items[0].published == "2026-10-17" and items[0].summary.startswith("ActiveSG Circle advised") and "<p>" not in items[0].summary
    kept = [i.title for i in items if relevant(i, ["traffic", "lrt", "corruption"], ["jail"])]
    assert kept == ["Cross Island Line: Traffic to be diverted in Ang Mo Kio Ave 3 amid suspension of tunnelling works",
                    "Bukit Panjang LRT upgrades to be completed by end-2026; work to replace power rails 98% done"]


def test_news_watch_reads_every_feed_per_feed_seen_and_a_failing_feed_never_stops_the_other(tmp_path, cfg, monkeypatch):
    import json

    import httpx

    import news_watch
    from db import Database
    from scrapers.base import FetchError
    from scrapers.lta_news import NewsItem, parse_feed

    lta_url, st_url = cfg["sources"]["lta_news_feed"], cfg["sources"]["media_news_feeds"]["The Straits Times"]
    st = parse_feed((FIX / "st_singapore_feed.xml").read_text(encoding="utf-8"))
    lta = [NewsItem("COE quota for November", "https://lta/1", "2026-10-10", "")]
    cfg["general"]["db_path"] = str(tmp_path / "cars.db")
    cfg["news"]["keywords"], cfg["news"]["exclude"] = ["coe", "traffic", "lrt", "cycling"], ["jail"]
    feeds, calls = {lta_url: lta, st_url: st}, []

    def run(self, url=None):
        calls.append(url)
        got = feeds[url]
        if isinstance(got, Exception):
            raise got
        return list(got)

    monkeypatch.setattr(news_watch, "load_config", lambda: cfg)
    monkeypatch.setattr(news_watch.LtaNewsScraper, "run", run)
    monkeypatch.setattr(news_watch, "FIRST_READ_CAP", 2)
    assert news_watch.main([]) == 0
    db = Database(cfg["general"]["db_path"])
    stored = json.loads(db.get_state(news_watch.NEWS_KEY))
    # first read of the press feed: all 5 links seen, only its 2 newest relevant items added, each with its source
    assert [(r["source"], r["title"][:18]) for r in stored] == [
        ("The Straits Times", "Cross Island Line:"), ("The Straits Times", "Bukit Panjang LRT "), ("LTA", "COE quota for Nove")]
    assert set(json.loads(db.get_state(f"news_seen {st_url}"))) == {i.link for i in st}
    assert json.loads(db.get_state(news_watch.SEEN_KEY)) == ["https://lta/1"]

    # LTA down: the press feed still adds its new item, and the not yet added CTE story stays seen, never added later
    feeds[lta_url] = FetchError("lta timeout")
    feeds[st_url] = [NewsItem("COE premiums rise", "https://st/new", "2026-10-18", "")] + st
    assert news_watch.main([]) == 0
    stored = json.loads(db.get_state(news_watch.NEWS_KEY))
    assert stored[0] == {"title": "COE premiums rise", "link": "https://st/new", "published": "2026-10-18", "summary": "", "source": "The Straits Times"}
    assert len(stored) == 4 and not any("CTE" in r["title"] for r in stored)

    # a 403 marks the press feed blocked for the day: not asked again, LTA still read
    resp = httpx.Response(403, request=httpx.Request("GET", st_url))
    blocked = FetchError("403")
    blocked.__cause__ = httpx.HTTPStatusError("403", request=resp.request, response=resp)
    feeds[lta_url], feeds[st_url] = lta, blocked
    news_watch.main([])
    assert json.loads(db.get_state(news_watch.BLOCKED_KEY)) == {st_url: date.today().isoformat()}
    calls.clear()
    news_watch.main([])
    assert calls == [lta_url]
    db.close()

    feeds[lta_url] = FetchError("down")       # every feed failing or blocked is a failed run
    assert news_watch.main([]) == 1


@pytest.mark.parametrize("st_resp", [
    (200, "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>captcha</body></html>"),
    (429, "slow down"),
])
def test_real_news_scraper_marks_a_challenge_page_or_429_blocked_and_never_asks_again(tmp_path, cfg, monkeypatch, st_resp):
    import json

    import httpx

    import news_watch
    import scrapers.base as base
    from db import Database

    cfg["general"].update(db_path=str(tmp_path / "cars.db"), cache_dir=str(tmp_path / "cache"), honour_robots_txt=False,
                          max_retries=1, throttle_seconds=0, throttle_jitter_seconds=0)
    st_url = cfg["sources"]["media_news_feeds"]["The Straits Times"]
    lta = (FIX / "lta_news_feed.xml").read_text(encoding="utf-8")
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(st_resp[0], text=st_resp[1]) if "straitstimes" in str(req.url) else httpx.Response(200, text=lta)

    orig = base.BaseScraper.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self.client = httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(base.BaseScraper, "_throttle", None)
    monkeypatch.setattr(base.BaseScraper, "__init__", init)
    monkeypatch.setattr(news_watch, "load_config", lambda: cfg)
    assert news_watch.main([]) == 0
    db = Database(cfg["general"]["db_path"])
    assert json.loads(db.get_state(news_watch.BLOCKED_KEY) or "{}") == {st_url: date.today().isoformat()}
    db.close()
    calls.clear()
    news_watch.main([])
    assert calls and not any("straitstimes" in c for c in calls)


def test_site_shows_the_news_source_and_old_items_are_lta(tmp_path, cfg, monkeypatch):
    import json

    import website
    from db import Database

    cfg["general"]["db_path"] = str(tmp_path / "cars.db")
    db = Database(cfg["general"]["db_path"])
    db.set_state("news_items", json.dumps([{"title": "a", "link": "https://st/a", "published": "2026-10-17", "summary": "", "source": "The Straits Times"},
                                           {"title": "b", "link": "https://lta/b", "published": "2026-10-01", "summary": ""}]))
    monkeypatch.setattr(website, "scrape_registrations", lambda *a, **k: None)
    monkeypatch.setattr(website, "scrape_body_types", lambda *a, **k: None)
    monkeypatch.setattr(website, "scrape_motorbikes", lambda *a, **k: {"used": {}, "brands": None})
    assert [n["source"] for n in website.build(cfg, db, date(2026, 10, 17), "ua")["news"]] == ["The Straits Times", "LTA"]
    db.close()
    page = (FIX.parent / "site" / "index.html").read_text(encoding="utf-8")
    assert '${esc(n.source || "LTA")} · ${dmyy(n.published)}' in page
