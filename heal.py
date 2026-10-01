"""Self repair through the Claude Code CLI in print mode (claude -p).

scheduler.py starts this as a child process when the daily report keeps failing after a retry,
usually because a site changed its layout and a parser no longer finds its fields. It:

1. snapshots every project file,
2. runs `claude -p` in the project folder with the failed sections, the log tail and the list of
   today's cached pages, limited by self_heal.allowed_tools, max_turns and max_budget_usd,
3. rejects the attempt if any file outside self_heal.editable changed, or a file was added
   outside self_heal.addable, or a file was deleted,
4. runs the test suite, then a dry run of the report against today's cached pages,
5. keeps the change only when the tests pass and the failed sections recover without new ones
   failing. Otherwise every file goes back to its snapshot.

Every attempt is saved under data/self_heal/<time>/ (prompt, CLI output, test output, patch,
result) and reported on Telegram. Exit code 0 means a change was kept and the report should run
again, 1 means nothing was kept.

  python heal.py            repair using the failures recorded by the last report run
"""
from __future__ import annotations

import difflib
import html
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("heal")

SKIP_DIRS = {"data", "logs", ".venv", ".git", "__pycache__", ".pytest_cache", "node_modules", ".mypy_cache"}


@dataclass
class HealResult:
    status: str  # kept, reverted, rejected, nochange, skipped, failed
    reason: str
    failing: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    summary: str = ""
    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    record_dir: str = ""


# Failure keys


def failing_keys(unavailable: dict[str, str] | None, ignore: list[str] | tuple = ()) -> list[str]:
    """Failed section keys that call for a repair, without the configured ignores."""
    return sorted(k for k in (unavailable or {}) if not any(i.lower() in k.lower() for i in ignore))


# Snapshot, diff and restore


def snapshot(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def changes(root: Path, before: dict[str, bytes]) -> tuple[list[str], list[str], list[str]]:
    after = snapshot(root)
    modified = sorted(p for p in before if p in after and after[p] != before[p])
    added = sorted(p for p in after if p not in before)
    deleted = sorted(p for p in before if p not in after)
    return modified, added, deleted


def violations(modified: list[str], added: list[str], deleted: list[str],
               editable: list[str], addable: list[str]) -> list[str]:
    """Why the change breaks the rules, empty when it keeps to them."""
    out = []
    for p in modified:
        if not any(p.startswith(e) for e in editable):
            out.append(f"changed {p}, outside {', '.join(editable)}")
    for p in added:
        if not any(p.startswith(e) for e in list(editable) + list(addable)):
            out.append(f"added {p}, outside {', '.join(list(editable) + list(addable))}")
    for p in deleted:
        out.append(f"deleted {p}")
    return out


def restore(root: Path, before: dict[str, bytes]) -> None:
    """Put every snapshotted file back and remove files that were added since."""
    after = snapshot(root)
    for p in after:
        if p not in before:
            (root / p).unlink(missing_ok=True)
    for p, data in before.items():
        target = root / p
        if not target.exists() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def patch_text(root: Path, before: dict[str, bytes], modified: list[str], added: list[str]) -> str:
    chunks = []
    for p in modified + added:
        old = before.get(p, b"")
        new = (root / p).read_bytes()
        try:
            a, b = old.decode("utf-8").splitlines(True), new.decode("utf-8").splitlines(True)
        except UnicodeDecodeError:
            chunks.append(f"Binary file {p} {'added' if p in added else 'changed'}\n")
            continue
        chunks.append("".join(difflib.unified_diff(a, b, f"a/{p}", f"b/{p}")))
    return "".join(chunks)


# Claude


def build_prompt(unavailable: dict[str, str], log_tail: str, cache_dir: Path, cfg: dict[str, Any]) -> str:
    sh = cfg.get("self_heal", {})
    editable, addable = sh.get("editable", ["scrapers/"]), sh.get("addable", ["fixtures/", "tests/"])
    failed = "\n".join(f"* {k}: {v}" for k, v in sorted(unavailable.items())) or "* the run crashed, see the log"
    return (
        "You are running unattended inside the SG car scraper container, in the project folder. "
        "Today's report run failed for these sections:\n"
        f"{failed}\n\n"
        "Diagnose why and fix the code so they work again.\n\n"
        f"Every page fetched today is cached under {cache_dir}/ as the text the scraper saw. Read the "
        "cached pages of the failing source to see its current layout. The end of logs/run.log is below.\n\n"
        "Rules:\n"
        f"* Only edit files under {', '.join(editable)}. You may add new files under {', '.join(addable)}, "
        "for example a copy of a cached page as a fixture and a test for the new layout. Do not edit "
        "config.yaml, existing tests or anything else.\n"
        "* Do not bypass robots.txt, throttling, CAPTCHAs, logins or other anti bot measures. If a site "
        "now blocks automated access, change nothing and say so.\n"
        "* If the failure is not a code problem (network down, site offline, Telegram or Claude login "
        "errors), change nothing and say so.\n"
        "* Keep the change small. Keep support for the old layout where you can, so existing tests pass.\n"
        "* Run `python -m pytest -q -x` before you finish. It must pass. You may run "
        "`python main.py --dry-run --section coe` (or new, used) to check the fix against today's cached pages.\n"
        "* Do not use git.\n"
        "* Finish with one short paragraph: what broke, what you changed and how you checked it. "
        "Do not use dashes as connectors.\n\n"
        "End of logs/run.log:\n"
        f"{log_tail}"
    )


def run_claude(cfg: dict[str, Any], root: Path, prompt: str) -> tuple[bool, str, float, str]:
    """(ok, summary, cost, raw output). ok is False when the CLI failed or reported an error."""
    sh = cfg.get("self_heal", {})
    command = cfg.get("ai", {}).get("command", "claude")
    args = [command, "-p", prompt, "--output-format", "json",
            "--max-turns", str(sh.get("max_turns", 60)),
            "--permission-mode", "acceptEdits",
            "--allowedTools", sh.get("allowed_tools", "Read,Grep,Glob,Edit,Write,Bash(python -m pytest:*)"),
            "--max-budget-usd", str(sh.get("max_budget_usd", 3.0)),
            "--no-session-persistence"]
    model = cfg.get("ai", {}).get("model")
    if model:
        args += ["--model", str(model)]
    try:
        proc = subprocess.run(args, cwd=root, capture_output=True, text=True,
                              timeout=int(sh.get("timeout_minutes", 40)) * 60, check=False)
    except subprocess.TimeoutExpired as exc:
        return False, "the Claude CLI ran out of time", 0.0, str(exc.stdout or "")
    except OSError as exc:
        return False, f"the Claude CLI could not start: {exc}", 0.0, ""
    raw = proc.stdout or ""
    try:
        env = json.loads(raw)
    except json.JSONDecodeError:
        env = {}
    cost = float(env.get("total_cost_usd") or 0) if isinstance(env, dict) else 0.0
    summary = str(env.get("result", "")).strip() if isinstance(env, dict) else ""
    if proc.returncode != 0 or (isinstance(env, dict) and env.get("is_error")):
        detail = summary or (proc.stderr or raw).strip()[:500]
        return False, f"the Claude CLI failed: {detail}", cost, raw + "\n" + (proc.stderr or "")
    return True, summary, cost, raw


def run_tests(cfg: dict[str, Any], root: Path) -> tuple[bool, str]:
    sh = cfg.get("self_heal", {})
    command = list(sh.get("test_command", ["python", "-m", "pytest", "-q", "-x"]))
    if command and command[0] == "python":
        command[0] = sys.executable
    try:
        proc = subprocess.run(command, cwd=root, capture_output=True, text=True,
                              timeout=int(sh.get("test_timeout_minutes", 20)) * 60, check=False)
    except subprocess.TimeoutExpired:
        return False, "tests ran out of time"
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-4000:]


def dry_run_failures(cfg: dict[str, Any], root: Path) -> Optional[dict[str, str]]:
    """Failed sections after a dry run against today's cached pages, None if the run crashed."""
    minutes = int(cfg.get("resilience", {}).get("max_run_minutes", 180))
    try:
        proc = subprocess.run([sys.executable, "main.py", "--dry-run"], cwd=root, capture_output=True,
                              text=True, timeout=minutes * 60, check=False)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return read_health(cfg, root).get("unavailable", {})


def read_health(cfg: dict[str, Any], root: Path) -> dict[str, Any]:
    from db import Database

    db = Database(root / cfg["general"]["db_path"])
    try:
        raw = db.get_state("last_run_health")
    finally:
        db.close()
    return json.loads(raw) if raw else {}


# The attempt


def attempt(cfg: dict[str, Any], root: Path, unavailable: dict[str, str], *,
            verify: Callable[[], Optional[dict[str, str]]] | None = None,
            claude: Callable[[str], tuple[bool, str, float, str]] | None = None,
            tests: Callable[[], tuple[bool, str]] | None = None,
            today: Optional[str] = None) -> HealResult:
    sh = cfg.get("self_heal", {})
    ignore = sh.get("ignore_unavailable", [])
    failing = failing_keys(unavailable, ignore)
    today = today or datetime.now().date().isoformat()
    record = root / "data" / "self_heal" / datetime.now().strftime("%Y%m%d-%H%M%S")
    record.mkdir(parents=True, exist_ok=True)

    def done(result: HealResult) -> HealResult:
        result.record_dir = str(record.relative_to(root))
        (record / "result.json").write_text(json.dumps(asdict(result), indent=2))
        return result

    if not sh.get("enabled", False):
        return done(HealResult("skipped", "self_heal.enabled is false", failing))
    if claude is None and shutil.which(cfg.get("ai", {}).get("command", "claude")) is None:
        return done(HealResult("skipped", "the claude command is not installed in the container", failing))

    log_path = root / cfg["general"]["log_dir"] / "run.log"
    log_tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-250:]) if log_path.exists() else "(no log)"
    prompt = build_prompt({k: unavailable[k] for k in failing} or unavailable, log_tail,
                          Path(cfg["general"]["cache_dir"]) / today, cfg)
    (record / "prompt.txt").write_text(prompt)

    before = snapshot(root)
    ok, summary, cost, raw = (claude or (lambda p: run_claude(cfg, root, p)))(prompt)
    (record / "claude.json").write_text(raw)
    modified, added, deleted = changes(root, before)
    base = dict(failing=failing, summary=summary, modified=modified, added=added, deleted=deleted, cost_usd=cost)

    if not (modified or added or deleted):
        status = "nochange" if ok else "failed"
        return done(HealResult(status, "Claude changed no files" if ok else summary, **base))
    patch = patch_text(root, before, modified, added)
    (record / "patch.diff").write_text(patch)
    bad = violations(modified, added, deleted, sh.get("editable", ["scrapers/"]), sh.get("addable", ["fixtures/", "tests/"]))
    if bad:
        restore(root, before)
        return done(HealResult("rejected", "; ".join(bad), **base))
    passed, output = (tests or (lambda: run_tests(cfg, root)))()
    (record / "tests.txt").write_text(output)
    if not passed:
        restore(root, before)
        return done(HealResult("reverted", "the tests failed with the change", **base))
    after = (verify or (lambda: dry_run_failures(cfg, root)))()
    if after is None:
        restore(root, before)
        return done(HealResult("reverted", "the dry run crashed with the change", **base))
    remaining = failing_keys(after, ignore)
    new = sorted(set(remaining) - set(failing))
    if new or not set(remaining) < set(failing) and failing:
        restore(root, before)
        why = f"new failures: {', '.join(new)}" if new else "the failed sections did not recover"
        return done(HealResult("reverted", why, remaining=remaining, **base))
    return done(HealResult("kept", "tests pass and the failed sections recovered", remaining=remaining, **base))


def notice(result: HealResult) -> str:
    """Telegram message for one attempt."""
    e = lambda s: html.escape(str(s), quote=False)  # noqa: E731
    verdict = {
        "kept": "Change kept", "reverted": "Change undone", "rejected": "Change refused and undone",
        "nochange": "No change made", "skipped": "Not attempted", "failed": "Repair failed",
    }.get(result.status, result.status)
    lines = [f"<b>Self repair: {e(verdict)}</b>", f"Failed today: {e(', '.join(result.failing) or 'the run crashed')}",
             f"Why: {e(result.reason)}"]
    if result.remaining and result.status == "kept":
        lines.append(f"Still failing: {e(', '.join(result.remaining))}")
    if result.modified or result.added:
        lines.append(f"Files: {e(', '.join(result.modified + result.added))}")
    if result.summary:
        lines.append(f"<blockquote expandable>{e(result.summary[:1500])}</blockquote>")
    if result.status == "kept":
        lines.append("The change is only on the NAS. Run git diff in the project folder to see it, "
                     "and send it on so it can go into the repository.")
    if result.record_dir:
        lines.append(f"Record: {e(result.record_dir)}")
    if result.cost_usd:
        lines.append(f"Claude cost: ${result.cost_usd:.2f}")
    return "\n".join(lines)


def main() -> int:
    import vault
    from main import setup_logging
    from settings import PROJECT_ROOT, load_config, load_secrets
    from telegram_bot import TelegramClient

    cfg = load_config()
    root = PROJECT_ROOT
    res = cfg.get("resilience", {})
    setup_logging(root / cfg["general"]["log_dir"], res.get("log_max_mb", 5), res.get("log_backups", 5), filename="heal.log")
    health = read_health(cfg, root)
    result = attempt(cfg, root, health.get("unavailable", {}))
    log.info("self repair %s: %s", result.status, result.reason)
    vault.event("🩹", f"self repair {result.status}", f"{', '.join(result.failing) or 'crashed run'}: {result.reason[:160]}")
    secrets = load_secrets()
    try:
        client = TelegramClient(secrets["telegram_bot_token"], secrets["telegram_chat_id"],
                                api_base=secrets.get("telegram_api_base"), thread_id=secrets.get("telegram_thread_id"))
        client.send_message(notice(result))
    except Exception as exc:
        log.warning("could not send the self repair notice: %s", exc)
    return 0 if result.status == "kept" else 1


if __name__ == "__main__":
    sys.exit(main())
