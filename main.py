"""Entry point. Runs the daily pipeline or the sample delivery test.

Examples:
  python main.py --dry-run                 build today's report and print it
  python main.py --sample                  send the hardcoded sample report to Telegram
  python main.py --section coe --dry-run   only the COE section
  python main.py --force                   ignore the page cache and resend even if sent today
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from rich.console import Console

from ai import report_text
from db import Database
from models import ReportSection
import json

from pipeline import Pipeline, should_send
from report import render_console, sample_report
from settings import load_config, load_secrets, user_agent
from telegram_bot import REPORT_BUTTONS, TelegramClient, TelegramError

SECTIONS = ("coe", "new", "used", "costs", "all")


def setup_logging(log_dir: Path, max_mb: float = 5, backups: int = 5, filename: str = "run.log") -> None:
    from logging.handlers import RotatingFileHandler

    log_dir.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stderr),
                RotatingFileHandler(log_dir / filename, maxBytes=int(max_mb * 1024 * 1024), backupCount=backups, encoding="utf-8")]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        # scheduler.py configures logging first; without force the file handler is never added.
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Singapore car market daily report")
    p.add_argument("--dry-run", action="store_true", help="print the report with rich instead of sending it")
    p.add_argument("--section", choices=SECTIONS, default="all", help="limit the run to one section")
    p.add_argument("--force", action="store_true", help="ignore the page cache and resend even if already sent today")
    p.add_argument("--since", type=date.fromisoformat, default=None, help="show changes since this date, YYYY-MM-DD")
    p.add_argument("--sample", action="store_true", help="use the hardcoded sample report for a delivery test")
    p.add_argument("--config", default=None, help="path to an alternative config.yaml")
    return p.parse_args(argv)


def build_report(cfg: dict, db: Database, run_date: date, section: str, since: date | None, force: bool) -> tuple[list[ReportSection], Pipeline]:
    """Run the scrapers and assemble the requested sections."""
    pipe = Pipeline(cfg, db, run_date, user_agent(cfg), force=force, since=since)
    return pipe.build(section), pipe


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()
    res = cfg.get("resilience", {})
    setup_logging(Path(cfg["general"]["log_dir"]), res.get("log_max_mb", 5), res.get("log_backups", 5))
    log = logging.getLogger("main")
    run_date = date.today()
    db = Database(cfg["general"]["db_path"])
    console = Console()

    try:
        pipe = None
        if args.sample:
            sections = sample_report(cfg, run_date)
        else:
            db.start_run(run_date)
            sections, pipe = build_report(cfg, db, run_date, args.section, args.since, args.force)
            # Read by scheduler.py and heal.py to decide on a retry or a self repair.
            db.set_state("last_run_health", json.dumps({
                "date": run_date.isoformat(), "section": args.section, "dry_run": args.dry_run,
                "unavailable": pipe.unavailable,
            }))
            if args.section == "all" and not args.dry_run:
                # Kept even when nothing is sent, so /ask always answers from today's figures.
                db.set_state("last_report", report_text([s.html for s in sections]))

        limit = cfg["telegram"]["max_message_length"]
        for s in sections:
            if len(s.html) > limit:
                log.warning("section %s is %d chars and will be split", s.key, len(s.html))

        if args.dry_run:
            render_console(sections, console)
            if pipe is not None:
                send, reasons, _ = should_send(cfg, db, pipe, run_date, args.force)
                verdict = "would send" if send else "would not send, nothing changed since the last report"
                console.print(f"[cyan]{verdict}[/cyan]" + (f": {'; '.join(reasons)}" if reasons else ""))
                db.finish_run(run_date, "dry-run")
            return 0

        if not args.sample and db.already_sent(run_date) and not args.force:
            console.print("[yellow]Report already sent today. Use --force to resend.[/yellow]")
            db.finish_run(run_date, "skipped")
            return 0

        signature = None
        if pipe is not None:
            send, reasons, signature = should_send(cfg, db, pipe, run_date, args.force)
            if not send:
                console.print("[yellow]Nothing changed since the last report. Not sending.[/yellow]")
                log.info("no changes since last sent report, skipping send")
                db.finish_run(run_date, "no-change")
                return 0
            log.info("sending because: %s", "; ".join(reasons))

        secrets = load_secrets()
        try:
            client = TelegramClient(
                secrets["telegram_bot_token"],
                secrets["telegram_chat_id"],
                parse_mode=cfg["telegram"]["parse_mode"],
                disable_preview=cfg["telegram"]["disable_web_page_preview"],
                api_base=secrets.get("telegram_api_base"),
                thread_id=secrets.get("telegram_thread_id"),
            )
            sent = client.send_many((s.html for s in sections), buttons=REPORT_BUTTONS)
        except TelegramError as exc:
            log.error("telegram delivery failed: %s", exc)
            if not args.sample:
                db.finish_run(run_date, "send-failed", str(exc))
            return 2
        console.print(f"[green]Sent {sent} Telegram messages.[/green]")
        if not args.sample:
            db.mark_sent(run_date)
            if signature is not None:
                db.set_state("last_sent_signature", json.dumps(signature))
            db.finish_run(run_date, "ok")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
