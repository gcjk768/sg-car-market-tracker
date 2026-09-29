import main
from scrapers.base import BaseScraper, FetchError


def _offline(monkeypatch):
    def boom(self, url, *a, **k):
        raise FetchError(f"offline test: {url}")

    monkeypatch.setattr(BaseScraper, "fetch", boom)
    monkeypatch.setattr(BaseScraper, "fetch_rendered", boom)


def test_sample_dry_run_exits_zero(tmp_config, capsys):
    assert main.main(["--sample", "--dry-run", "--config", str(tmp_config)]) == 0
    out = capsys.readouterr().out
    assert "SAMPLE DATA" in out
    assert "considerations" in out


def test_pipeline_dry_run_marks_missing_sections(tmp_config, capsys, monkeypatch):
    _offline(monkeypatch)
    assert main.main(["--dry-run", "--section", "coe", "--config", str(tmp_config)]) == 0
    assert "unavailable today" in capsys.readouterr().out


def test_full_pipeline_offline_produces_every_section(tmp_config, capsys, monkeypatch):
    _offline(monkeypatch)
    assert main.main(["--dry-run", "--config", str(tmp_config)]) == 0
    out = capsys.readouterr().out
    for key in ("summary", "coe", "new_ev", "used_ev", "used_ice", "costs", "considerations"):
        assert key in out
    assert "Unavailable today" in out


def test_send_without_token_fails_cleanly(tmp_config, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main.main(["--sample", "--config", str(tmp_config)]) == 2


def test_pipeline_merges_cnergy_board(tmp_config, monkeypatch):
    from datetime import date
    from pathlib import Path

    import pipeline as pl
    from db import Database
    from models import FuelPrice
    from settings import load_config

    cfg = load_config(tmp_config)
    monkeypatch.setattr(pl, "scrape_fuel_price", lambda *a, **k: FuelPrice(observed_on=date(2026, 9, 29), ron95_per_litre=3.48, by_brand={"SPC": 3.46, "Shell": 3.49}, source="t"))
    monkeypatch.setattr(pl, "scrape_cnergy", lambda *a, **k: {"95": {"public": 2.64, "member": 2.54}, "diesel": {"public": 1.80}})
    db = Database(cfg["general"]["db_path"])
    p = pl.Pipeline(cfg, db, date(2026, 9, 29), "ua")
    p.run_fuel()
    latest = db.latest_fuel_price()
    assert latest.by_brand["Cnergy"] == 2.54
    assert latest.station_prices["diesel"] == {"public": 1.80}
    assert latest.ron95_per_litre == 3.46   # median of 2.54, 3.46, 3.49
    db.close()
