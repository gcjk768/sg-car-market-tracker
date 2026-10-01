import re
from datetime import date, datetime

import pytest

import vault
from models import CoeCategory, CoeResult, Drivetrain, UsedListing

TODAY = date(2026, 10, 2)
LINE = re.compile(r"^- \d\d:\d\d \S+ \*\*[^*]+\*\*( · [^·]+)*( · \[\[[^\]]+\]\])?$")


@pytest.fixture
def vdir(tmp_path, monkeypatch):
    d = tmp_path / "SG Car Market"
    monkeypatch.setenv("VAULT_DIR", str(d))
    return d


def car(price=118800, first_seen=TODAY, **kw):
    return UsedListing(source="sgcarmart", listing_id="1234567", url="https://example.com/1234567", make="BYD",
                       model="Atto 3", drivetrain=Drivetrain.ev, year=2023, price=price, first_seen=first_seen,
                       last_seen=TODAY, **kw)


def history(path):
    return path.read_text(encoding="utf-8").split("## History\n")[1].strip().splitlines()


def test_activity_line_format():
    when = datetime(2026, 10, 2, 8, 5, tzinfo=vault.SGT)
    assert vault.activity_line("🆕", "new listing", "BYD Atto 3\n$118,800", "BYD Atto 3 2023 — sgcarmart 1", when) == \
        "- 08:05 🆕 **new listing** · BYD Atto 3 $118,800 · [[BYD Atto 3 2023 — sgcarmart 1]]"
    assert vault.activity_line("✅", "report sent", when=when) == "- 08:05 ✅ **report sent**"


def test_event_appends_to_todays_note(vdir):
    when = datetime(2026, 10, 2, 8, 0, tzinfo=vault.SGT)
    vault.event("▶️", "run started", "section all", when=when)
    vault.event("✅", "report sent", "9 messages", when=when)
    note = vdir / "Activity" / "2026-10-02.md"
    text = note.read_text(encoding="utf-8")
    assert text.startswith("---\ntags: [active]\nupdated: 2026-10-02\n---\n# Activity 2026-10-02")
    lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert lines == ["- 08:00 ▶️ **run started** · section all", "- 08:00 ✅ **report sent** · 9 messages"]
    assert all(LINE.match(ln) for ln in lines)
    assert (vdir / "Home.md").exists() and (vdir / "Cars").is_dir() and (vdir / "COE").is_dir()


def test_disabled_without_vault_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_DIR", raising=False)
    vault.event("✅", "report sent")
    vault.track_car(car(), TODAY)
    assert vault.memory() == "" and not vault.enabled()


def test_car_note_price_history_is_append_only(vdir):
    vault.track_car(car(), TODAY)
    note = vdir / "Cars" / "BYD Atto 3 2023 — sgcarmart 1234567.md"
    assert history(note) == ["- 2026-10-02 🆕 listed at $118,800"]
    assert "price: 118800" in note.read_text(encoding="utf-8")

    vault.track_car(car(), date(2026, 10, 3))  # same price, nothing to add
    assert len(history(note)) == 1

    vault.track_car(car(price=115800), date(2026, 10, 4))
    vault.track_car(car(price=116800), date(2026, 10, 5))
    assert history(note) == ["- 2026-10-02 🆕 listed at $118,800", "- 2026-10-04 🟢 price $115,800 (-$3,000)",
                             "- 2026-10-05 🔴 price $116,800 (+$1,000)"]
    activity = (vdir / "Activity").glob("*.md")
    lines = [ln for f in activity for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
    assert any("**new listing**" in ln and "[[BYD Atto 3 2023 — sgcarmart 1234567]]" in ln for ln in lines)
    assert any("**price drop** · BYD Atto 3 $118,800 to $115,800" in ln for ln in lines)


def test_listing_seen_before_the_vault_is_tracked_without_a_new_alert(vdir):
    vault.track_car(car(first_seen=date(2026, 9, 20)), TODAY)
    note = next((vdir / "Cars").glob("*.md"))
    assert history(note) == ["- 2026-10-02 📌 tracking at $118,800, first seen 2026-09-20"]
    assert not list((vdir / "Activity").glob("*.md"))


def test_reported_once_unless_price_changes(vdir):
    vault.track_car(car(), TODAY)
    assert not vault.already_reported(car())
    assert vault.cars_reported([car()], TODAY) == 1
    assert vault.already_reported(car())
    assert vault.cars_reported([car()], TODAY) == 0  # not flagged again
    assert not vault.already_reported(car(price=110000))
    vault.track_car(car(price=110000), TODAY)
    assert vault.cars_reported([car(price=110000)], TODAY) == 1
    note = next((vdir / "Cars").glob("*.md"))
    assert history(note)[-1] == "- 2026-10-02 📣 reported again at $110,000"


def test_gone_listing_is_archived(vdir, tmp_path):
    from db import Database

    db = Database(tmp_path / "cars.db")
    db.upsert_used_listings([car(first_seen=None)], date(2026, 9, 25))
    vault.track_car(db.active_listings()[0], date(2026, 9, 25))
    vault.cars_gone(db, "sgcarmart", TODAY)
    db.close()
    note = next((vdir / "Cars").glob("*.md"))
    text = note.read_text(encoding="utf-8")
    assert "tags:\n- archived" in text
    assert history(note)[-1] == "- 2026-10-02 ❌ gone, last $118,800, 7 days on market"


def test_coe_history_written_once_per_tender(vdir):
    r = CoeResult(tender_date=date(2026, 9, 24), exercise="2026-09 second exercise", category=CoeCategory.A,
                  quota_premium=131890, quota=1000, bids_received=1500, source="lta")
    vault.coe_result(r, -1110)
    vault.coe_result(r, -1110)
    note = vdir / "COE" / "Cat A.md"
    assert history(note) == ["- 2026-09-24 2026-09 second exercise: $131,890 (-$1,110), bids 1,500 for quota 1,000"]


def test_memory_is_capped_newest_first_and_has_the_car(vdir):
    for i in range(400):
        vault.event("🔎", "scraped sgcarmart ev", f"run {i:03d}", when=datetime(2026, 10, 2, 8, 0, tzinfo=vault.SGT))
    vault.track_car(car(), TODAY)
    vault.track_car(car(price=115800), TODAY)
    mem = vault.memory(cars=[car()], chars=vault.MEMORY_CHARS)
    assert len(mem) <= 4000
    assert mem.index("price $115,800") < mem.index("listed at $118,800")  # newest first
    assert "first seen 2026-10-02" in mem
    assert "run 399" in mem and "run 000" not in mem  # the newest activity survives the cap
    assert vault.memory(query="Is the BYD Atto 3 worth it?").count("BYD Atto 3 2023 — sgcarmart 1234567,") == 1
    assert "sgcarmart 1234567, first seen" not in vault.memory(query="what about the MG4?")


def test_memory_reaches_the_prompts(vdir, cfg, monkeypatch, tmp_path):
    from ai import ClaudeCli, analyst_note, report_text
    from bot_listener import ask_text
    from db import Database
    from tests.test_ai import _fake_run

    vault.track_car(car(), TODAY)
    cfg["ai"]["enabled"] = True
    cfg["general"]["db_path"] = str(tmp_path / "cars.db")
    monkeypatch.setattr("ai.shutil.which", lambda cmd: "/usr/bin/claude")
    run, calls = _fake_run({"result": "Fine."})
    monkeypatch.setattr("ai.subprocess.run", run)

    analyst_note(ClaudeCli(cfg), {"coe": {"A": 1}}, vault.memory(cars=[car()]))
    assert "listed at $118,800" in calls[0]["args"][2] and "do not flag it again" in calls[0]["args"][2]

    db = Database(cfg["general"]["db_path"])
    db.set_state("last_report", report_text(["<b>Used EV</b>"]))
    db.close()
    assert ask_text("Is the BYD Atto 3 a good buy?", cfg) == "Fine."
    assert "listed at $118,800" in calls[1]["args"][2]
    assert calls[1]["input"] == "Used EV"  # the report is still the whole standard input
    assert "**/ask** · Is the BYD Atto 3 a good buy? · answered" in (vdir / "Activity").joinpath(
        f"{vault.now_sgt():%Y-%m-%d}.md").read_text(encoding="utf-8")


def test_vault_errors_never_raise(tmp_path, monkeypatch):
    blocker = tmp_path / "not a folder"
    blocker.write_text("x")
    monkeypatch.setenv("VAULT_DIR", str(blocker))  # every write fails: the path is a file
    vault.event("✅", "report sent")
    vault.track_car(car(), TODAY)
    vault.cars_gone(None, "sgcarmart", TODAY)
    vault.coe_result(None)
    assert vault.cars_reported([car()], TODAY) == 0
    assert vault.already_reported(car()) is False
    assert vault.memory(cars=[car()]) == ""


def test_report_html_is_the_same_with_the_vault_on(cfg, tmp_path, monkeypatch):
    """The vault only reads and writes notes: the Telegram sections are byte for byte the same."""
    import pipeline as pl
    from db import Database
    from scrapers.base import BaseScraper, FetchError

    def boom(self, url, *a, **k):
        raise FetchError("offline test")

    monkeypatch.setattr(BaseScraper, "fetch", boom)
    monkeypatch.setattr(BaseScraper, "fetch_rendered", boom)
    out = {}
    for mode in ("off", "on"):
        if mode == "on":
            monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
        cfg["general"]["cache_dir"] = str(tmp_path / mode / "cache")
        db = Database(tmp_path / mode / "cars.db")
        db.upsert_used_listings([car(first_seen=None)], TODAY)
        out[mode] = [s.html for s in pl.Pipeline(cfg, db, TODAY, "ua").build("all")]
        db.close()
    assert out["on"] == out["off"]
    assert list((tmp_path / "vault" / "Cars").glob("*.md"))
