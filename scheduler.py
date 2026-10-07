"""Container entry point and supervisor. Keeps the report arriving without anyone watching.

* Runs the daily report at general.schedule_time as a child process, stopped after
  resilience.max_run_minutes, so a hung browser page cannot freeze the service.
* A run that crashes, cannot send, or leaves sections unavailable is retried after
  resilience.retry_after_minutes. When it fails again and self_heal is on, heal.py asks the
  Claude CLI to repair the code, keeps the change only if it is safe and works, and the report
  runs again. After every retry and repair, one Telegram alert a day says what still fails.
* Runs bot_listener.py as a second child process and restarts it with backoff if it exits.
* Writes data/heartbeat every tick. `python scheduler.py --health` checks it for Docker's
  healthcheck, and a watchdog thread exits the process if the loop itself stops ticking, so
  Docker's restart policy starts it fresh.
* Once a day, removes page cache folders and self repair records past their keep days.
* On start, catches up a report the container missed while it was down.

Environment:
  RUN_ON_START=1   run a report as soon as the container starts
  RUN_LISTENER=1   also run bot_listener.py so /run, /coe, /filters and /ask work
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from settings import PROJECT_ROOT, load_config

log = logging.getLogger("scheduler")

DONE_STATUSES = ("ok", "no-change", "skipped")


def next_run(now: datetime, schedule_time: str) -> datetime:
    """Next occurrence of HH:MM in the same time zone as `now`, strictly after `now`."""
    hour, minute = (int(x) for x in schedule_time.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


@dataclass
class Child:
    kind: str  # report, heal or listener
    proc: Any  # subprocess.Popen, or a test double with poll() and kill()
    started: datetime
    deadline: Optional[datetime]
    reason: str = ""


def spawn_python(script: str, args: list[str], root: Path) -> subprocess.Popen:
    """Start `python script args` in its own process group, so a kill also stops Chromium."""
    return subprocess.Popen([sys.executable, script, *args], cwd=root, start_new_session=True)


def kill_tree(proc: Any) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:
            pass


class Supervisor:
    def __init__(self, cfg: dict[str, Any], root: Path = PROJECT_ROOT, *,
                 spawn: Callable[[str, list[str]], Any] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 notify: Callable[[str], None] | None = None,
                 listener: bool = False):
        self.cfg = cfg
        self.root = Path(root)
        self.res = cfg.get("resilience", {})
        self.tz = ZoneInfo(cfg["general"].get("timezone", "Asia/Singapore"))
        self.schedule_time = str(cfg["general"].get("schedule_time", "08:00"))
        self.spawn = spawn or (lambda script, args: spawn_python(script, args, self.root))
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.notify = notify or self._telegram
        self.want_listener = listener
        now = self.clock()
        self.next_daily = next_run(now, self.schedule_time)
        self.child: Optional[Child] = None
        self.listener: Optional[Child] = None
        self.listener_backoff = 30
        self.coe_watcher: Optional[Child] = None
        self.coe_watch_day: Optional[date] = None
        self.jobs: dict[str, dict] = {}  # periodic one shot scripts: {name: {"proc", "started", "next"}}
        self.listener_next: Optional[datetime] = now
        self.retry_at: Optional[datetime] = None
        self.day: Optional[date] = None
        self.failures_today = 0
        self.heals_today = 0
        self.alerted_today = False
        self.last_tick = time.monotonic()
        self.stopping = False

    # Loop

    def run_forever(self) -> None:
        self._start_watchdog()
        while not self.stopping:
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed, carrying on")
            time.sleep(int(self.res.get("tick_seconds", 30)))

    def tick(self) -> None:
        now = self.clock()
        self.last_tick = time.monotonic()
        self._new_day(now)
        self._heartbeat(now)
        self._supervise_listener(now)
        self._coe_watch(now)
        self._periodic("news_watch.py", now, self._news_due(now), 10)
        self._periodic("bid_capture.py", now, self._capture_due(now), 10)
        if self.child is not None:
            self._check_child(now)
            return
        if now >= self.next_daily:
            self.next_daily = next_run(now, self.schedule_time)
            self.start_report("daily")
        elif self.retry_at is not None and now >= self.retry_at:
            self.retry_at = None
            self.start_report("retry")

    # Result day COE push

    def _coe_watch(self, now: datetime) -> None:
        push = self.cfg.get("coe", {}).get("push", {})
        if not push.get("enabled"):
            return
        if self.coe_watcher is not None:
            if self.coe_watcher.proc.poll() is None:
                if now >= self.coe_watcher.deadline:
                    kill_tree(self.coe_watcher.proc)
                return
            self.coe_watcher = None
        from scrapers.coe import ensure_schedule, tender_result_dates
        from settings import user_agent

        coe = self.cfg["coe"]
        today = now.date()
        ensure_schedule(self.cfg, user_agent(self.cfg), today)
        if today not in tender_result_dates(today.year, today.month, tuple(coe["tender_weeks_of_month"]), coe["results_weekday"]):
            return
        h, m = (int(x) for x in str(push.get("start", "16:00")).split(":"))
        eh, em = (int(x) for x in str(push.get("stop", "16:30")).split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if self.coe_watch_day == today or not (start <= now < end):
            return
        self.coe_watch_day = today
        log.info("starting COE watcher")
        self.coe_watcher = Child("coe", self.spawn("coe_watch.py", []), now, end + timedelta(minutes=5))

    # Periodic one shot jobs: LTA news a few times a day, the live bidding page while bidding is open

    def _periodic(self, script: str, now: datetime, due_every: Optional[int], limit_minutes: int) -> None:
        job = self.jobs.setdefault(script, {"proc": None, "started": now, "next": now})
        if job["proc"] is not None:
            if job["proc"].poll() is None:
                if now - job["started"] > timedelta(minutes=limit_minutes):
                    kill_tree(job["proc"])
                return
            job["proc"] = None
        if due_every is None or now < job["next"]:
            return
        job["next"], job["started"] = now + timedelta(minutes=due_every), now
        job["proc"] = self.spawn(script, [])

    def _news_due(self, now: datetime) -> Optional[int]:
        news = self.cfg.get("news")
        if not news:
            return None
        (h1, m1), (h2, m2) = ([int(x) for x in t.split(":")] for t in news.get("window", ["08:00", "22:00"]))
        return int(news.get("interval_minutes", 180)) if (h1, m1) <= (now.hour, now.minute) < (h2, m2) else None

    def _capture_due(self, now: datetime) -> Optional[int]:
        cap = self.cfg.get("coe", {}).get("live_capture", {})
        if not cap.get("enabled"):
            return None
        from coe_forecast import bidding_window
        from scrapers.coe import ensure_schedule, next_tender_date
        from settings import user_agent

        ensure_schedule(self.cfg, user_agent(self.cfg), now.date())
        coe = self.cfg["coe"]
        result_day = next_tender_date(now.date() - timedelta(days=3), tuple(coe["tender_weeks_of_month"]), coe["results_weekday"])
        opens, closes = bidding_window(result_day, self.cfg)
        now_naive = now.replace(tzinfo=None)
        return int(cap.get("poll_minutes", 30)) if opens <= now_naive < closes + timedelta(minutes=45) else None

    # Reports and repairs

    def start_report(self, reason: str) -> None:
        now = self.clock()
        deadline = now + timedelta(minutes=int(self.res.get("max_run_minutes", 180)))
        log.info("starting report (%s)", reason)
        self.child = Child("report", self.spawn("main.py", []), now, deadline, reason)

    def start_heal(self) -> None:
        now = self.clock()
        minutes = int(self.cfg.get("self_heal", {}).get("timeout_minutes", 40)) + int(self.res.get("max_run_minutes", 180)) + 30
        self.heals_today += 1
        log.warning("starting self repair")
        self.child = Child("heal", self.spawn("heal.py", []), now, now + timedelta(minutes=minutes), "repair")

    def _check_child(self, now: datetime) -> None:
        child = self.child
        code = child.proc.poll()
        if code is None:
            if child.deadline and now >= child.deadline:
                log.error("%s ran past its time limit, stopping it", child.kind)
                kill_tree(child.proc)
                code = -9
            else:
                return
        self.child = None
        if child.kind == "report":
            self._after_report(code, now)
        elif child.kind == "heal":
            self._after_heal(code, now)

    def _after_report(self, code: int, now: datetime) -> None:
        from heal import failing_keys

        failing = failing_keys(self._health().get("unavailable", {}),
                               self.cfg.get("self_heal", {}).get("ignore_unavailable", []))
        if code == 0 and not failing:
            log.info("report finished cleanly")
            self.failures_today = 0
            return
        what = self._describe(code, failing)
        self.failures_today += 1
        log.warning("report failed (%s), failure %d today", what, self.failures_today)
        telegram_only = code == 2 and not failing
        sh = self.cfg.get("self_heal", {})
        can_heal = (not telegram_only and sh.get("enabled", False)
                    and self.heals_today < int(sh.get("max_attempts_per_day", 1)))
        if self.failures_today >= 2 and can_heal:
            self.start_heal()
            return
        if self.failures_today <= int(self.res.get("max_retries_per_day", 2)):
            self.retry_at = now + timedelta(minutes=int(self.res.get("retry_after_minutes", 60)))
            log.info("retry at %s", self.retry_at.isoformat(timespec="minutes"))
            return
        self._alert(what)

    def _after_heal(self, code: int, now: datetime) -> None:
        if code == 0:
            log.info("self repair kept a change, running the report again")
            self.start_report("after repair")
            return
        log.warning("self repair kept nothing (exit %s)", code)
        if self.failures_today <= int(self.res.get("max_retries_per_day", 2)):
            self.retry_at = now + timedelta(minutes=int(self.res.get("retry_after_minutes", 60)))
        else:
            self._alert("the report still fails and self repair could not fix it")

    @staticmethod
    def _describe(code: int, failing: list[str]) -> str:
        parts = []
        if code == -9:
            parts.append("ran past its time limit")
        elif code == 2:
            parts.append("Telegram delivery failed")
        elif code:
            parts.append(f"crashed with exit code {code}")
        if failing:
            parts.append("unavailable: " + ", ".join(failing))
        return "; ".join(parts) or "unknown"

    def _alert(self, what: str) -> None:
        if self.alerted_today or not self.res.get("alert_on_failure", True):
            return
        self.alerted_today = True
        nxt = self.next_daily.strftime("%a %d %b %H:%M")
        text = (f"<b>Daily report problem</b>\nThe report still fails after every retry: {_escape(what)}.\n"
                f"Next scheduled run: {nxt}. Details in logs/run.log on the NAS.")
        log.error("alerting: %s", what)
        try:
            self.notify(text)
        except Exception as exc:
            log.warning("alert could not be sent: %s", exc)

    # Listener

    def _supervise_listener(self, now: datetime) -> None:
        if not self.want_listener:
            return
        if self.listener is not None:
            code = self.listener.proc.poll()
            if code is None:
                if now - self.listener.started > timedelta(minutes=30):
                    self.listener_backoff = 30  # it has been up a while, so reset the backoff
                return
            log.warning("listener exited with code %s, restarting in %d s", code, self.listener_backoff)
            self.listener = None
            self.listener_next = now + timedelta(seconds=self.listener_backoff)
            self.listener_backoff = min(self.listener_backoff * 2, 600)
        if self.listener_next is not None and now >= self.listener_next:
            self.listener = Child("listener", self.spawn("bot_listener.py", []), now, None)
            self.listener_next = None
            log.info("listener started")

    # Day boundary, heartbeat, housekeeping, catch up

    def _new_day(self, now: datetime) -> None:
        if self.day == now.date():
            return
        self.day = now.date()
        self.failures_today = 0
        self.heals_today = 0
        self.alerted_today = False
        try:
            self.housekeeping(now.date())
        except Exception:
            log.exception("housekeeping failed")

    def _heartbeat(self, now: datetime) -> None:
        path = self.root / "data" / "heartbeat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(now.isoformat(timespec="seconds"))

    def housekeeping(self, today: date) -> list[str]:
        """Remove dated folders past their keep days. Returns what was removed."""
        removed = []
        targets = [(self.root / self.cfg["general"]["cache_dir"], int(self.res.get("cache_keep_days", 7))),
                   (self.root / "data" / "self_heal", int(self.res.get("self_heal_keep_days", 30)))]
        for folder, keep in targets:
            if not folder.is_dir():
                continue
            for entry in folder.iterdir():
                # Page cache folders are 2026-09-30, self repair records 20260930-070101.
                m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})(?:$|-\d{6}$)", entry.name)
                if not m:
                    continue
                try:
                    day = date(int(m[1]), int(m[2]), int(m[3]))
                except ValueError:
                    continue
                if entry.is_dir() and (today - day).days > keep:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed.append(str(entry.relative_to(self.root)))
        if removed:
            log.info("housekeeping removed %s", ", ".join(removed))
        return removed

    def catch_up(self, run_on_start: bool) -> None:
        """Run now when asked to, or when today's scheduled run was missed while the container was down."""
        if run_on_start:
            self.start_report("on start")
            return
        now = self.clock()
        todays = next_run(now - timedelta(days=1), self.schedule_time)
        if todays.date() == now.date() and now >= todays and self._today_status(now.date()) not in DONE_STATUSES:
            self.start_report("missed while down")

    def _today_status(self, day: date) -> Optional[str]:
        from db import Database

        try:
            db = Database(self.root / self.cfg["general"]["db_path"])
            try:
                row = db.conn.execute("SELECT status FROM runs WHERE run_date = ?", (day.isoformat(),)).fetchone()
            finally:
                db.close()
            return row["status"] if row else None
        except Exception:
            return None

    def _health(self) -> dict[str, Any]:
        from db import Database

        try:
            db = Database(self.root / self.cfg["general"]["db_path"])
            try:
                raw = db.get_state("last_run_health")
            finally:
                db.close()
            return json.loads(raw) if raw else {}
        except Exception:
            log.exception("could not read the last run's health")
            return {}

    # Watchdog and shutdown

    def _start_watchdog(self) -> None:
        limit = int(self.res.get("watchdog_minutes", 15)) * 60

        def watch() -> None:
            while not self.stopping:
                time.sleep(30)
                if time.monotonic() - self.last_tick > limit:
                    log.critical("scheduler loop stopped ticking for %d s, exiting for a clean restart", limit)
                    self.stop_children()
                    os._exit(1)

        threading.Thread(target=watch, name="watchdog", daemon=True).start()

    def stop_children(self) -> None:
        for child in (self.child, self.listener, self.coe_watcher):
            if child is not None and child.proc.poll() is None:
                kill_tree(child.proc)

    def _telegram(self, text: str) -> None:
        from settings import load_secrets
        from telegram_bot import TelegramClient

        s = load_secrets()
        TelegramClient(s["telegram_bot_token"], s["telegram_chat_id"], api_base=s.get("telegram_api_base"),
                       thread_id=s.get("telegram_thread_id")).send_message(text)


def _escape(text: str) -> str:
    import html

    return html.escape(text, quote=False)


def healthy(root: Path, cfg: dict[str, Any], now: Optional[datetime] = None) -> bool:
    """True when the heartbeat is fresher than the watchdog limit. Used by Docker's healthcheck."""
    path = root / "data" / "heartbeat"
    try:
        beat = datetime.fromisoformat(path.read_text().strip())
    except (OSError, ValueError):
        return False
    now = now or datetime.now(beat.tzinfo)
    return (now - beat).total_seconds() < int(cfg.get("resilience", {}).get("watchdog_minutes", 15)) * 60


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = load_config()
    if "--health" in argv:
        return 0 if healthy(PROJECT_ROOT, cfg) else 1
    from main import setup_logging

    res = cfg.get("resilience", {})
    setup_logging(PROJECT_ROOT / cfg["general"]["log_dir"], res.get("log_max_mb", 5), res.get("log_backups", 5),
                  filename="scheduler.log")
    sup = Supervisor(cfg, PROJECT_ROOT, listener=os.getenv("RUN_LISTENER") == "1")

    def stop(signum, frame):  # docker stop sends SIGTERM
        log.info("stopping on signal %s", signum)
        sup.stopping = True
        sup.stop_children()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    sup.catch_up(os.getenv("RUN_ON_START") == "1")
    log.info("next daily report at %s", sup.next_daily.isoformat(timespec="minutes"))
    sup.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
