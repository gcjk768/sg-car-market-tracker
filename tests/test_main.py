import main


def test_sample_dry_run_exits_zero(tmp_config, capsys):
    assert main.main(["--sample", "--dry-run", "--config", str(tmp_config)]) == 0
    out = capsys.readouterr().out
    assert "SAMPLE DATA" in out
    assert "considerations" in out


def test_pipeline_dry_run_marks_missing_sections(tmp_config, capsys):
    assert main.main(["--dry-run", "--section", "coe", "--config", str(tmp_config)]) == 0
    assert "unavailable today" in capsys.readouterr().out


def test_send_without_token_fails_cleanly(tmp_config, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main.main(["--sample", "--config", str(tmp_config)]) == 2
