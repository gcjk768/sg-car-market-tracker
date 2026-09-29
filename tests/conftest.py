import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    with open(ROOT / "config.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    # Tests never call the real Claude CLI. Tests that exercise the AI path enable it and mock it.
    data.setdefault("ai", {})["enabled"] = False
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
