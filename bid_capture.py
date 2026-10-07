"""Save the LTA open bidding page as it looks right now, so the layout while bidding is open can be studied.
The scheduler runs this every few minutes between a tender's opening and just after its close. Nothing here
is stored as a COE result: a live premium is not a result.

  python bid_capture.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import vault
from scrapers.base import BaseScraper
from scrapers.coe import parse_coe_tables
from settings import PROJECT_ROOT, load_config, user_agent

log = logging.getLogger("bid_capture")


class Capture(BaseScraper):
    name = "bid_capture"

    def parse(self, text: str) -> list:
        return []

    def run(self) -> list:
        return []


def banner(html: str) -> str:
    """The sentence that says whether an exercise is open, running or over."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"[A-Z]+ \d{4} \d\w{2} Open Bidding Exercise (?:has|is|will)[^.]{0,90}", text)
    return m.group(0).strip() if m else ""


def main() -> int:
    cfg = load_config()
    tz = ZoneInfo(cfg["general"].get("timezone", "Asia/Singapore"))
    now = datetime.now(tz)
    cap = Capture(cfg, user_agent(cfg), now.date(), force=True)
    try:
        html = cap.fetch_rendered(cfg["sources"]["onemotoring_coe"], wait_selector="table")
    finally:
        cap.close()
    folder = PROJECT_ROOT / "data" / "live_bidding" / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(html.encode()).hexdigest()[:12]
    log_path = folder / "log.jsonl"
    last = log_path.read_text(encoding="utf-8").splitlines()[-1:] if log_path.exists() else []
    if last and json.loads(last[0])["sha"] == digest:
        return 0  # page unchanged since the last capture
    (folder / f"{now:%H%M}.html").write_text(html, encoding="utf-8")
    rows = {c: v for c, v in parse_coe_tables(html).items()}
    entry = {"at": now.isoformat(timespec="seconds"), "sha": digest, "banner": banner(html), "rows": rows}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    summary = ", ".join(f"{c} {v.get('premium'):,}" for c, v in sorted(rows.items()) if v.get("premium"))
    vault.event("📸", "live bidding snapshot", f"{entry['banner'][:90]} · {summary}")
    log.info("saved %s", folder / f"{now:%H%M}.html")
    return 0


if __name__ == "__main__":
    from main import setup_logging
    cfg = load_config()
    setup_logging(PROJECT_ROOT / cfg["general"]["log_dir"], 5, 5, filename="bid_capture.log")
    sys.exit(main())
