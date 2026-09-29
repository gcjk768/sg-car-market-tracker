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
