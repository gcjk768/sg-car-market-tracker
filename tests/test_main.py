from pathlib import Path

import main
from scrapers.base import BaseScraper, FetchError


def _offline(monkeypatch):
    def boom(self, url, *a, **k):
        raise FetchError(f"offline test: {url}")

    monkeypatch.setattr(BaseScraper, "fetch", boom)
    monkeypatch.setattr(BaseScraper, "fetch_rendered", boom)


def _health(tmp_config):
    import json

    from db import Database
    from settings import load_config

    cfg = load_config(tmp_config)
    db = Database(cfg["general"]["db_path"])
    try:
        health = json.loads(db.get_state("last_run_health"))
        status = db.conn.execute("SELECT status FROM runs").fetchone()["status"]
    finally:
        db.close()
    return cfg, health, status


def test_dry_run_records_health_and_leaves_the_site_alone(tmp_config, monkeypatch):
    _offline(monkeypatch)
    assert main.main(["--dry-run", "--section", "coe", "--config", str(tmp_config)]) == 0
    cfg, health, status = _health(tmp_config)
    assert set(health) == {"date", "section", "dry_run", "unavailable"}
    assert health["section"] == "coe" and health["dry_run"] is True and "coe" in health["unavailable"]
    assert status == "dry-run"
    assert not Path(cfg["site"]["output"]).exists()


def test_full_run_offline_writes_the_site_data(tmp_config, monkeypatch):
    import json

    _offline(monkeypatch)
    assert main.main(["--config", str(tmp_config)]) == 0
    cfg, health, status = _health(tmp_config)
    assert status == "ok" and health["dry_run"] is False
    assert {"coe", "new_ev"} <= set(health["unavailable"])
    data = json.loads(Path(cfg["site"]["output"]).read_text(encoding="utf-8"))
    assert data["news"] == [] and data["used"] == []


def test_site_write_failure_exits_2(tmp_config, monkeypatch):
    import website

    _offline(monkeypatch)
    monkeypatch.setattr(website, "build", lambda *a, **k: 1 / 0)
    assert main.main(["--section", "coe", "--config", str(tmp_config)]) == 2
    assert _health(tmp_config)[2] == "site-failed"


def test_pipeline_merges_cnergy_board(tmp_config, monkeypatch):
    from datetime import date

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
