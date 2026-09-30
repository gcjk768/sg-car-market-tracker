import json
import subprocess
from datetime import date
from pathlib import Path

from ai import ClaudeCli, analyst_note, extract_listing_fields
from models import Drivetrain
from scrapers.used_sgcarmart import SgcarmartUsedScraper

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def _fake_run(result: dict | str, returncode: int = 0):
    calls = []

    def run(args, input="", capture_output=True, text=True, timeout=0, check=False):
        calls.append({"args": args, "input": input})
        stdout = json.dumps(result) if isinstance(result, dict) else result
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    return run, calls


def test_disabled_when_switched_off(cfg):
    cfg["ai"]["enabled"] = False
    cli = ClaudeCli(cfg)
    assert cli.available() is False
    assert cli.ask("hi") is None


def test_ask_parses_cli_envelope_and_counts_calls(cfg, monkeypatch):
    cfg["ai"].update({"enabled": True, "model": "sonnet", "max_calls_per_run": 2})
    monkeypatch.setattr("ai.shutil.which", lambda cmd: "/usr/bin/claude")
    run, calls = _fake_run({"result": "```json\n{\"price\": 118800, \"mileage_km\": 28000}\n```", "total_cost_usd": 0.01})
    monkeypatch.setattr("ai.subprocess.run", run)
    cli = ClaudeCli(cfg)
    assert cli.ask_json("read this", "page text") == {"price": 118800, "mileage_km": 28000}
    assert calls[0]["args"][:3] == ["claude", "-p", "read this"]
    assert "--output-format" in calls[0]["args"] and "--max-turns" in calls[0]["args"]
    assert "--tools" in calls[0]["args"] and "--no-session-persistence" in calls[0]["args"]
    assert calls[0]["args"][calls[0]["args"].index("--model") + 1] == "sonnet"
    assert calls[0]["input"] == "page text"
    assert cli.ask("again") is not None
    assert cli.ask("over budget") is None
    assert cli.calls == 2 and cli.total_cost_usd == 0.02


def test_cli_error_and_bad_json_are_swallowed(cfg, monkeypatch):
    cfg["ai"]["enabled"] = True
    monkeypatch.setattr("ai.shutil.which", lambda cmd: "/usr/bin/claude")
    run, _ = _fake_run({"result": "boom", "is_error": True})
    monkeypatch.setattr("ai.subprocess.run", run)
    assert ClaudeCli(cfg).ask("x") is None
    run, _ = _fake_run("not json at all")
    monkeypatch.setattr("ai.subprocess.run", run)
    assert ClaudeCli(cfg).ask_json("x") is None
    run, calls = _fake_run({"result": "Not logged in"}, returncode=1)
    monkeypatch.setattr("ai.subprocess.run", run)
    cli = ClaudeCli(cfg)
    assert cli.ask("x") is None
    assert cli.ask("y") is None
    assert len(calls) == 1


def test_extract_listing_fields_keeps_known_keys_only(cfg, monkeypatch):
    cfg["ai"]["enabled"] = True
    monkeypatch.setattr("ai.shutil.which", lambda cmd: "/usr/bin/claude")
    run, _ = _fake_run({"result": json.dumps({"price": 99000, "colour": "red", "flags": ["as is"], "owners": None})})
    monkeypatch.setattr("ai.subprocess.run", run)
    assert extract_listing_fields(ClaudeCli(cfg), "text") == {"price": 99000, "flags": ["as is"]}


def test_parser_falls_back_to_ai_only_when_fields_are_missing(cfg, monkeypatch):
    cfg["ai"]["enabled"] = True
    monkeypatch.setattr("ai.shutil.which", lambda cmd: "/usr/bin/claude")
    run, calls = _fake_run({"result": json.dumps({"mileage_km": 31000, "coe_expiry": "2033-03-11", "fuel_type": "electric", "flags": ["no warranty"]})})
    monkeypatch.setattr("ai.subprocess.run", run)
    cli = ClaudeCli(cfg)
    s = SgcarmartUsedScraper(cfg, "ua", date(2026, 9, 29), group="ev", ai=cli)
    card = {"listing_id": "1", "url": "https://x/1", "title": "BYD Atto 3", "price": 118800}
    # Complete page: no call made.
    full = s.parse_detail((FIX / "sgcarmart_used_detail.html").read_text(), card)
    assert full.mileage_km == 28000 and calls == []
    # Page with the mileage and COE rows removed: the model fills them.
    html = (FIX / "sgcarmart_used_detail.html").read_text()
    html = "\n".join(l for l in html.splitlines() if "Mileage" not in l and "<td>COE</td>" not in l)
    partial = s.parse_detail(html, card)
    assert len(calls) == 1
    assert partial.mileage_km == 31000
    assert partial.coe_expiry == date(2033, 3, 11) and 6.4 < partial.coe_years_remaining < 6.5
    assert partial.drivetrain == Drivetrain.ev
    assert "no warranty" in partial.flags
    assert partial.price == 118800  # existing values are never overwritten


def test_analyst_note(cfg, monkeypatch):
    cfg["ai"]["enabled"] = True
    monkeypatch.setattr("ai.shutil.which", lambda cmd: "/usr/bin/claude")
    run, calls = _fake_run({"result": "Cat A is high. The used EV is cheapest to own. Wait for October."})
    monkeypatch.setattr("ai.subprocess.run", run)
    note = analyst_note(ClaudeCli(cfg), {"coe": {"A": 131890}})
    assert note.startswith("Cat A is high")
    assert json.loads(calls[0]["input"]) == {"coe": {"A": 131890}}


def test_ask_answers_from_saved_report(cfg, monkeypatch, tmp_path):
    from ai import report_text
    from bot_listener import ask_text
    from db import Database

    cfg["ai"]["enabled"] = True
    cfg["general"]["db_path"] = str(tmp_path / "cars.db")
    monkeypatch.setattr("ai.shutil.which", lambda cmd: "/usr/bin/claude")
    run, calls = _fake_run({"result": "Buy the Atto 3 & keep it 7 years."})
    monkeypatch.setattr("ai.subprocess.run", run)

    assert "No report saved" in ask_text("which car?", cfg)
    db = Database(cfg["general"]["db_path"])
    db.set_state("last_report", report_text(["<b>Used EV</b>\n1. <a href='x'>BYD Atto 3</a> $118,800 &amp; more"]))
    db.close()

    assert "for example" in ask_text("", cfg)
    assert ask_text("which car?", cfg) == "Buy the Atto 3 &amp; keep it 7 years."
    assert calls[0]["args"][2].endswith("Question: which car?")
    assert calls[0]["input"] == "Used EV\n1. BYD Atto 3 $118,800 & more"
