"""Configuration and secrets loading.

Config values come from config.yaml. Secrets come from environment variables, with a .env
file loaded when present. Nothing here touches the network.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Read config.yaml and return it as a plain dictionary."""
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("config.yaml must contain a mapping at the top level")
    return data


@lru_cache(maxsize=1)
def config() -> dict[str, Any]:
    """Cached config for the process. Call load_config directly in tests that need a custom file."""
    return load_config()


def load_secrets() -> dict[str, str | None]:
    """Load .env if present and return the secrets the project uses."""
    load_dotenv(PROJECT_ROOT / ".env")
    return {
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        # Only for tests and local mocks. Leave unset to talk to api.telegram.org.
        "telegram_api_base": os.getenv("TELEGRAM_API_BASE"),
        "scraper_contact": os.getenv("SCRAPER_CONTACT", "no contact set"),
    }


def user_agent(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or config()
    contact = load_secrets()["scraper_contact"]
    return cfg["general"]["user_agent"].format(contact=contact)
