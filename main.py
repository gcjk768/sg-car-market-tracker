"""Entry point. Runs the daily scrape and writes the website data (data/site/data.json).

Examples:
  python main.py                           scrape everything, store it and rebuild the site data
  python main.py --dry-run                 scrape and record the run's health, leave the site alone
  python main.py --section coe --dry-run   only the COE scrape
  python main.py --force                   ignore the page cache

Exit codes: 0 success, 2 the site data could not be written.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import vault
import website
from db import Database
from pipeline import Pipeline
from settings import load_config, user_agent

SECTIONS = ("coe", "new", "used", "all")


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
    p = argparse.ArgumentParser(description="Singapore car market daily scrape for the website")
    p.add_argument("--dry-run", action="store_true", help="scrape and record the run's health, do not write the site data")
    p.add_argument("--section", choices=SECTIONS, default="all", help="limit the run to one group of scrapers")
    p.add_argument("--force", action="store_true", help="ignore the page cache")
    p.add_argument("--config", default=None, help="path to an alternative config.yaml")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()
    res = cfg.get("resilience", {})
    setup_logging(Path(cfg["general"]["log_dir"]), res.get("log_max_mb", 5), res.get("log_backups", 5))
    log = logging.getLogger("main")
    run_date = date.today()
    db = Database(cfg["general"]["db_path"])
    try:
        db.start_run(run_date)
        vault.event("▶️", "run started", f"section {args.section}" + (", dry run" if args.dry_run else ""))
        pipe = Pipeline(cfg, db, run_date, user_agent(cfg), force=args.force)
        pipe.run(args.section)
        # Read by scheduler.py and heal.py to decide on a retry or a self repair.
        db.set_state("last_run_health", json.dumps({
            "date": run_date.isoformat(), "section": args.section, "dry_run": args.dry_run,
            "unavailable": pipe.unavailable,
        }))
        failed = ", ".join(sorted(pipe.unavailable))
        if failed:
            vault.event("⚠️", "sections unavailable", failed)
            log.warning("unavailable today: %s", failed)
        if args.dry_run:
            db.finish_run(run_date, "dry-run")
            vault.event("🧪", "dry run finished", failed and f"unavailable: {failed}")
            return 0
        if website.publish(cfg, db, run_date, user_agent(cfg), force=args.force) is None:
            db.finish_run(run_date, "site-failed")
            vault.event("🚨", "run finished", "site data not written" + (f"; unavailable: {failed}" if failed else ""))
            return 2
        db.finish_run(run_date, "ok")
        vault.event("✅", "run finished", "site data written" + (f"; unavailable: {failed}" if failed else ""))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
