"""Optional long polling listener for ad hoc commands. Run it only when you want it:

  uv run python bot_listener.py

Commands: /run sends a full report now, /coe sends only the COE table, /filters shows the
current used car filters. Messages from any chat other than TELEGRAM_CHAT_ID are ignored.
"""
from __future__ import annotations

import logging
import sys
from datetime import date

from db import Database
from pipeline import Pipeline
from report import SECTION_TITLES
from settings import load_config, load_secrets, user_agent
from telegram_bot import TelegramClient, escape

log = logging.getLogger("bot_listener")


def filters_text(cfg: dict) -> str:
    f = cfg["used"]["filters"]
    lines = [
        "<b>Current used car filters</b>",
        f"Price ceiling: {f['price_ceiling_sgd']:,}",
        f"Mileage: at most {f['max_km_per_year_of_age']:,} km per year of age",
        f"Owners: at most {f['max_owners']}",
        f"COE remaining: at least {f['min_coe_years_remaining']} years",
        f"Age: EV under {f['max_age_years']['ev']}, petrol or hybrid under {f['max_age_years']['ice']}",
        "Excluded words: " + escape(", ".join(f["exclude_keywords"])),
        "Bonus words: " + escape(", ".join(f.get("bonus_keywords", []))),
        f"Loan: {cfg['costs']['financing']['flat_rate_new'] * 100:.2f}% new, {cfg['costs']['financing']['flat_rate_used'] * 100:.2f}% used, flat, up to {cfg['costs']['financing']['max_tenure_years']} years",
        "Edit config.yaml to change them.",
    ]
    return "\n".join(lines)


def handle(command: str, cfg: dict, client: TelegramClient) -> None:
    if command == "/filters":
        client.send_message(filters_text(cfg))
        return
    section = "coe" if command == "/coe" else "all"
    db = Database(cfg["general"]["db_path"])
    try:
        sections = Pipeline(cfg, db, date.today(), user_agent(cfg)).build(section)
        client.send_many(s.html for s in sections)
    finally:
        db.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    secrets = load_secrets()
    client = TelegramClient(secrets["telegram_bot_token"], secrets["telegram_chat_id"], api_base=secrets.get("telegram_api_base"))
    chat_id = str(secrets["telegram_chat_id"])
    offset = None
    log.info("listening for /run, /coe and /filters")
    while True:
        try:
            updates = client.get_updates(offset=offset, timeout=30)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            log.warning("getUpdates failed: %s", exc)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != chat_id:
                continue
            text = (msg.get("text") or "").strip().split("@")[0].lower()
            if text in ("/run", "/coe", "/filters"):
                log.info("command %s", text)
                try:
                    handle(text, cfg, client)
                except Exception as exc:
                    log.exception("command failed")
                    client.send_message(f"Command failed: {escape(str(exc))}")
            elif text.startswith("/"):
                client.send_message("Commands: /run, /coe, /filters")


if __name__ == "__main__":
    sys.exit(main())
