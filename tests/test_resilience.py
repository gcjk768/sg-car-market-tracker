import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import heal
from db import Database
from scheduler import Supervisor, healthy

SG = ZoneInfo("Asia/Singapore")


class FakeProc:
    def __init__(self, code=None):
        self.code = code
        self.killed = False
        self.pid = -1

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True
        self.code = -9


class Harness:
    """A supervisor with a movable clock, recorded spawns and recorded alerts."""

    def __init__(self, cfg, tmp_path, listener=False):
        cfg["general"]["db_path"] = "data/cars.db"
        cfg["general"]["cache_dir"] = "data/cache"
        cfg["self_heal"]["enabled"] = True
        self.now = datetime(2026, 9, 30, 7, 0, tzinfo=SG)
        self.spawned: list[tuple[str, FakeProc]] = []
        self.alerts: list[str] = []
        self.root = tmp_path
        self.sup = Supervisor(cfg, tmp_path, spawn=self.spawn, clock=lambda: self.now,
                              notify=self.alerts.append, listener=listener)

    def spawn(self, script, args):
        proc = FakeProc()
        self.spawned.append((script, proc))
        return proc

    def health(self, unavailable):
        db = Database(self.root / "data" / "cars.db")
        db.set_state("last_run_health", json.dumps({"unavailable": unavailable}))
        db.close()

    def finish(self, code, unavailable=None):
        self.health(unavailable or {})
        self.spawned[-1][1].code = code
        self.sup.tick()

    def at(self, hh, mm=0, day=30):
        self.now = datetime(2026, 9, day, hh, mm, tzinfo=SG)
        self.sup.tick()

    @property
    def scripts(self):
        return [s for s, _ in self.spawned]


def test_daily_run_starts_at_schedule_time(cfg, tmp_path):
    h = Harness(cfg, tmp_path)
    h.at(7, 59)
    assert h.scripts == []
    h.at(8, 0)
    assert h.scripts == ["main.py"]
    assert (tmp_path / "data" / "heartbeat").read_text().startswith("2026-09-30T08:00")


def test_clean_run_needs_nothing_more(cfg, tmp_path):
    h = Harness(cfg, tmp_path)
    h.at(8)
    h.finish(0)
    h.at(12)
    assert h.scripts == ["main.py"] and h.alerts == []


def test_failure_retries_then_repairs_then_reruns(cfg, tmp_path):
    h = Harness(cfg, tmp_path)
    h.at(8)
    h.finish(0, {"used sgcarmart ev": "no listings found"})  # degraded run
    h.at(8, 30)
    assert h.scripts == ["main.py"]  # waits for the retry time
    h.at(9, 1)
    assert h.scripts == ["main.py", "main.py"]  # retry
    h.finish(0, {"used sgcarmart ev": "no listings found"})  # same failure again
    assert h.scripts[-1] == "heal.py"
    h.finish(0)  # repair kept a change
    assert h.scripts[-1] == "main.py"  # report runs again straight away
    h.finish(0, {})
    assert h.alerts == []


def test_failed_repair_leads_to_one_alert(cfg, tmp_path):
    h = Harness(cfg, tmp_path)
    h.at(8)
    h.finish(1)  # crash
    h.at(9, 1)
    h.finish(1)  # crash again, repair starts
    assert h.scripts[-1] == "heal.py"
    h.finish(1)  # repair kept nothing, one more retry
    h.at(10, 5)
    assert h.scripts[-1] == "main.py"
    h.finish(1)
    assert len(h.alerts) == 1 and "crashed with exit code 1" in h.alerts[0]
    h.at(11, 30)
    assert len(h.alerts) == 1  # once a day


def test_telegram_failure_is_retried_but_never_repaired(cfg, tmp_path):
    h = Harness(cfg, tmp_path)
    h.at(8)
    h.finish(2)
    h.at(9, 1)
    h.finish(2)
    h.at(10, 2)
    h.finish(2)
    assert "heal.py" not in h.scripts
    assert len(h.alerts) == 1 and "Telegram delivery failed" in h.alerts[0]


def test_hung_run_is_stopped_at_its_time_limit(cfg, tmp_path, monkeypatch):
    import scheduler

    monkeypatch.setattr(scheduler, "kill_tree", lambda proc: proc.kill())
    h = Harness(cfg, tmp_path)
    h.at(8)
    h.health({})
    h.at(10, 59)
    assert not h.spawned[0][1].killed
    h.at(11, 1)  # max_run_minutes is 180
    assert h.spawned[0][1].killed
    assert h.sup.retry_at is not None


def test_listener_is_restarted_with_backoff(cfg, tmp_path):
    h = Harness(cfg, tmp_path, listener=True)
    h.at(7, 0)
    assert h.scripts == ["bot_listener.py"]
    h.spawned[0][1].code = 1  # listener crashed
    h.at(7, 0)
    assert h.scripts == ["bot_listener.py"]  # waits 30 s
    h.now += timedelta(seconds=31)
    h.sup.tick()
    assert h.scripts == ["bot_listener.py", "bot_listener.py"]
    assert h.sup.listener_backoff == 60


def test_new_day_resets_counters_and_prunes_old_folders(cfg, tmp_path):
    for name in ("data/cache/2026-09-01", "data/cache/2026-09-29", "data/self_heal/20260801-080000",
                 "data/self_heal/20260929-080000", "data/cache/notes"):
        (tmp_path / name).mkdir(parents=True)
    h = Harness(cfg, tmp_path)
    h.at(7)
    left = sorted(p.relative_to(tmp_path).as_posix() for p in (tmp_path / "data").glob("*/*"))
    assert left == ["data/cache/2026-09-29", "data/cache/notes", "data/self_heal/20260929-080000"]
    h.sup.failures_today = 3
    h.now = datetime(2026, 10, 1, 7, 0, tzinfo=SG)
    h.sup.tick()
    assert h.sup.failures_today == 0


def test_catch_up_after_missing_the_morning(cfg, tmp_path):
    h = Harness(cfg, tmp_path)
    h.now = datetime(2026, 9, 30, 9, 30, tzinfo=SG)
    h.sup.catch_up(run_on_start=False)
    assert h.scripts == ["main.py"]
    h2 = Harness(cfg, tmp_path / "early")
    h2.now = datetime(2026, 9, 30, 7, 30, tzinfo=SG)
    h2.sup.catch_up(run_on_start=False)
    assert h2.scripts == []


def test_health_check_reads_the_heartbeat(cfg, tmp_path):
    assert healthy(tmp_path, cfg) is False
    (tmp_path / "data").mkdir()
    beat = datetime(2026, 9, 30, 8, 0, tzinfo=SG)
    (tmp_path / "data" / "heartbeat").write_text(beat.isoformat())
    assert healthy(tmp_path, cfg, beat + timedelta(minutes=5)) is True
    assert healthy(tmp_path, cfg, beat + timedelta(minutes=20)) is False


# Self repair


@pytest.fixture
def project(tmp_path):
    (tmp_path / "scrapers").mkdir()
    (tmp_path / "scrapers" / "coe.py").write_text("PREMIUM = 'old'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_coe.py").write_text("def test(): pass\n")
    (tmp_path / "config.yaml").write_text("x: 1\n")
    (tmp_path / "data").mkdir()
    return tmp_path


def heal_cfg(cfg):
    cfg["self_heal"]["enabled"] = True
    cfg["general"]["log_dir"] = "logs"
    return cfg


def fake_claude(project, edits):
    def run(prompt):
        for rel, text in edits.items():
            path = project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return True, "COE table moved into a new div, parser updated.", 0.42, "{}"
    return run


def test_repair_is_kept_when_tests_pass_and_sections_recover(cfg, project):
    result = heal.attempt(heal_cfg(cfg), project, {"coe": "no COE table found"},
                          claude=fake_claude(project, {"scrapers/coe.py": "PREMIUM = 'new'\n",
                                                       "fixtures/coe_new_layout.html": "<table/>"}),
                          tests=lambda: (True, "3 passed"), verify=lambda: {})
    assert result.status == "kept"
    assert result.modified == ["scrapers/coe.py"] and result.added == ["fixtures/coe_new_layout.html"]
    assert (project / "scrapers" / "coe.py").read_text() == "PREMIUM = 'new'\n"
    record = project / result.record_dir
    assert "+PREMIUM = 'new'" in (record / "patch.diff").read_text()
    assert json.loads((record / "result.json").read_text())["status"] == "kept"
    assert "no COE table found" in (record / "prompt.txt").read_text()
    text = heal.notice(result)
    assert "Change kept" in text and "scrapers/coe.py" in text and "$0.42" in text


def test_edits_outside_the_scraper_code_are_refused_and_undone(cfg, project):
    result = heal.attempt(heal_cfg(cfg), project, {"coe": "x"},
                          claude=fake_claude(project, {"config.yaml": "x: 2\n", "tests/test_coe.py": "def test(): assert 1\n"}),
                          tests=lambda: (True, ""), verify=lambda: {})
    assert result.status == "rejected"
    assert "changed config.yaml" in result.reason and "changed tests/test_coe.py" in result.reason
    assert (project / "config.yaml").read_text() == "x: 1\n"
    assert (project / "tests" / "test_coe.py").read_text() == "def test(): pass\n"


def test_failing_tests_undo_the_change_including_new_files(cfg, project):
    result = heal.attempt(heal_cfg(cfg), project, {"coe": "x"},
                          claude=fake_claude(project, {"scrapers/coe.py": "broken\n", "scrapers/extra.py": "y\n"}),
                          tests=lambda: (False, "1 failed"), verify=lambda: {})
    assert result.status == "reverted" and "tests failed" in result.reason
    assert (project / "scrapers" / "coe.py").read_text() == "PREMIUM = 'old'\n"
    assert not (project / "scrapers" / "extra.py").exists()


def test_change_that_does_not_fix_the_section_is_undone(cfg, project):
    claude = fake_claude(project, {"scrapers/coe.py": "PREMIUM = 'other'\n"})
    result = heal.attempt(heal_cfg(cfg), project, {"coe": "x"}, claude=claude,
                          tests=lambda: (True, ""), verify=lambda: {"coe": "still broken"})
    assert result.status == "reverted" and "did not recover" in result.reason
    claude = fake_claude(project, {"scrapers/coe.py": "PREMIUM = 'other'\n"})
    result = heal.attempt(heal_cfg(cfg), project, {"coe": "x"}, claude=claude,
                          tests=lambda: (True, ""), verify=lambda: {"new_ev": "broke"})
    assert result.status == "reverted" and "new failures: new_ev" in result.reason
    assert (project / "scrapers" / "coe.py").read_text() == "PREMIUM = 'old'\n"


def test_no_change_and_disabled_cases(cfg, project):
    result = heal.attempt(heal_cfg(cfg), project, {"coe": "site offline"},
                          claude=lambda p: (True, "The site is offline, nothing to fix.", 0.1, "{}"),
                          tests=lambda: (True, ""), verify=lambda: {})
    assert result.status == "nochange"
    cfg["self_heal"]["enabled"] = False
    assert heal.attempt(cfg, project, {"coe": "x"}).status == "skipped"


def test_ignored_failures_are_not_repair_targets():
    assert heal.failing_keys({"coe": "a", "fuel Cnergy": "b"}, ["cnergy"]) == ["coe"]


def test_real_cli_arguments(cfg, project, monkeypatch):
    seen = {}

    class Done:
        returncode = 0
        stdout = json.dumps({"result": "fixed", "total_cost_usd": 0.5})
        stderr = ""

    def fake_run(args, cwd=None, **kw):
        seen["args"], seen["cwd"] = args, cwd
        return Done()

    monkeypatch.setattr(heal.subprocess, "run", fake_run)
    ok, summary, cost, _ = heal.run_claude(heal_cfg(cfg), project, "fix it")
    args = seen["args"]
    assert ok and summary == "fixed" and cost == 0.5 and seen["cwd"] == project
    assert args[:3] == ["claude", "-p", "fix it"]
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert "Bash(python -m pytest:*)" in args[args.index("--allowedTools") + 1]
    assert "git" not in args[args.index("--allowedTools") + 1]
    assert args[args.index("--max-budget-usd") + 1] == "3.0"


def test_real_child_process_tree_is_killed(tmp_path):
    import time

    from scheduler import kill_tree, spawn_python

    (tmp_path / "sleepy.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "time.sleep(300)\n")
    proc = spawn_python("sleepy.py", [], tmp_path)
    time.sleep(1)
    assert proc.poll() is None
    kill_tree(proc)
    proc.wait(timeout=10)
    assert proc.returncode is not None
