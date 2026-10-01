"""Optional long polling listener for ad hoc commands. Run it only when you want it:

  uv run python bot_listener.py

Commands: /run sends a full report now, /coe sends only the COE table, /filters shows the
current used car filters, /ask <question> answers from the latest report through the Claude
CLI. Messages from any chat other than TELEGRAM_CHAT_ID are ignored.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import date

import vault
from ai import ClaudeCli, answer_question
from db import Database
from pipeline import Pipeline
from report import SECTION_TITLES
from settings import load_config, load_secrets, user_agent
from telegram_bot import REPORT_BUTTONS, TelegramClient, escape

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


def ask_text(question: str, cfg: dict) -> str:
    if not question:
        return "Ask a question after the command, for example: /ask Is a used Atto 3 better value than a new MG4?"
    db = Database(cfg["general"]["db_path"])
    try:
        report = db.get_state("last_report")
    finally:
        db.close()
    if not report:
        return "No report saved yet. Try again after the next daily run, or send /run first."
    cli = ClaudeCli(cfg)
    cli.max_chars = int(cfg.get("ai", {}).get("ask_max_input_chars", 40000))
    if not cli.available():
        return "AI is off. Set ai.enabled in config.yaml and sign the Claude CLI in on the NAS."
    answer = answer_question(cli, question, report, vault.memory(query=question))
    vault.event("💬", "/ask", f"{question[:80]} · {'answered' if answer else 'no answer'}")
    return escape(answer) if answer else "The Claude CLI did not answer. It is probably not signed in on the NAS, see logs/run.log."


def handle(command: str, cfg: dict, client: TelegramClient, arg: str = "") -> None:
    if command == "/ask":
        client.send_message(ask_text(arg, cfg))
        return
    if command == "/filters":
        client.send_message(filters_text(cfg))
        vault.event("💬", "/filters", "answered")
        return
    section = "coe" if command == "/coe" else "all"
    db = Database(cfg["general"]["db_path"])
    try:
        vault.event("💬", command, f"{section} report requested")
        today = date.today()
        pipe = Pipeline(cfg, db, today, user_agent(cfg))
        sections = pipe.build(section)
        sent = client.send_many((s.html for s in sections), buttons=REPORT_BUTTONS if section == "all" else None)
        vault.event("✅", f"{command} sent", f"{sent} messages")
        vault.cars_reported([l for l, _ in pipe.used_ev + pipe.used_ice], today)
    finally:
        db.close()


COMMANDS = ("/run", "/coe", "/filters", "/ask")
# Only these may come from a button. callback_data is client supplied, so it is not trusted further.
BUTTON_COMMANDS = ("/run", "/coe")


def parse_update(u: dict, chat_id: str, thread_id: int | None) -> tuple[str, str, str | None] | None:
    """(command, argument, callback id) for a typed command or a button press in our chat and topic, else None."""
    cq = u.get("callback_query")
    msg = (cq or {}).get("message") or u.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != chat_id:
        return None
    if thread_id and msg.get("message_thread_id") != thread_id:
        return None
    if cq:
        data = cq.get("data")
        return (data, "", cq["id"]) if data in BUTTON_COMMANDS else None
    # "/ask@bot question" or "/ask question": the command is lower cased, the question kept.
    head, _, arg = (msg.get("text") or "").strip().partition(" ")
    return head.split("@")[0].lower(), arg.strip(), None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    secrets = load_secrets()
    client = TelegramClient(
        secrets["telegram_bot_token"], secrets["telegram_chat_id"],
        api_base=secrets.get("telegram_api_base"), thread_id=secrets.get("telegram_thread_id"),
    )
    chat_id = str(secrets["telegram_chat_id"])
    offset = None
    log.info("listening for /run, /coe, /filters and /ask")
    while True:
        try:
            # Long poll shorter than the 30 s HTTP timeout, or every idle poll ends in a ReadTimeout.
            updates = client.get_updates(offset=offset, timeout=20)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            # A persistent error (409 conflict, network down) would otherwise spin this loop.
            log.warning("getUpdates failed: %s", exc)
            time.sleep(10)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            parsed = parse_update(u, chat_id, client.thread_id)
            if not parsed:
                continue
            text, arg, callback_id = parsed
            if callback_id:
                try:
                    client.answer_callback(callback_id, "Working on it")
                except Exception:
                    log.exception("could not answer the button press")
            if text in COMMANDS:
                log.info("command %s", text)
                try:
                    handle(text, cfg, client, arg)
                except Exception as exc:
                    log.exception("command failed")
                    try:
                        client.send_message(f"Command failed: {escape(str(exc))}")
                    except Exception:
                        log.exception("could not report the failure")  # stay up, the next command may work
            elif text.startswith("/"):
                try:
                    client.send_message("Commands: /run, /coe, /filters, /ask &lt;question&gt;")
                except Exception:
                    log.exception("could not send the command list")


if __name__ == "__main__":
    sys.exit(main())
