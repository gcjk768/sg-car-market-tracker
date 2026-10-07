import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_real_env(monkeypatch):
    """Never read the project's .env in tests. On the NAS it holds the real bot token, and a test
    that removed TELEGRAM_BOT_TOKEN got it back from .env and posted to the live topic."""
    monkeypatch.setattr("settings.load_dotenv", lambda *a, **k: None)
    # The container sets VAULT_DIR to the live Obsidian vault; tests (run by heal.py there) must not write to it.
    monkeypatch.delenv("VAULT_DIR", raising=False)
    # The schedule PDF is fetched once per process in production; tests use the rule unless they load a schedule themselves.
    monkeypatch.setattr("scrapers.coe.ensure_schedule", lambda *a, **k: None)
    monkeypatch.setattr("pipeline.ensure_schedule", lambda *a, **k: None, raising=False)


@pytest.fixture
def cfg():
    with open(ROOT / "config.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    # Tests never call the real Claude CLI. Tests that exercise the AI path enable it and mock it.
    data.setdefault("ai", {})["enabled"] = False
    data["ai"]["command"] = "claude"   # the deployed command may be a wrapper (Ollama stand-in); tests assert the plain CLI
    return data


@pytest.fixture
def tmp_config(tmp_path, cfg):
    """A copy of config.yaml whose database, cache and logs point at a temp directory."""
    cfg["general"]["db_path"] = str(tmp_path / "cars.db")
    cfg["general"]["cache_dir"] = str(tmp_path / "cache")
    cfg["general"]["log_dir"] = str(tmp_path / "logs")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path
