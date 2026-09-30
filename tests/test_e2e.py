"""End to end: the real pipeline against a local web server that serves the fixtures, and the
real Telegram client against a local mock of the Bot API."""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

import main
from scrapers.base import BaseScraper
from telegram_bot import MAX_MESSAGE_LENGTH

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def route(path: str) -> str | None:
    """Map a request path to a fixture file, imitating each site's URL patterns."""
    p = path.lower()
    rules = [
        ("robots.txt", None),
        ("m03", "lta_m03.xlsx"),
        ("coe", "onemotoring_coe.html"),
        ("info.php", "sgcarmart_used_detail.html"),
        ("/used-cars/info/", "sgcarmart_used_detail.html"),
        ("/used_cars/listing", "sgcarmart_used_list.html"),
        ("/sg/en/buy/", "carro_used_detail.html"),
        ("/sg/en/buy", "carro_used_list.html"),
        ("/used-car/", "motorist_used_detail.html"),
        ("/used-cars/electric-car", "motorist_used_list.html"),
        ("/used-cars/gac", "motorist_used_detail.html"),
        ("/used-cars", "motorist_used_list.html"),
        ("carcode=", "sgcarmart_new_model.html"),
        ("/new-cars/model/", "sgcarmart_new_model.html"),
        ("/new_cars/", "sgcarmart_new_index.html"),
        ("petrol", "motorist_fuel.html"),
        ("cnergy", "cnergy_prices.html"),
        ("brand", "tesla_brand_page.html"),
    ]
    for needle, fixture in rules:
        if needle in p:
            return fixture
    return None


class Handler(BaseHTTPRequestHandler):
    sent: list[dict] = []

    def log_message(self, *a):  # keep pytest output quiet
        pass

    def do_GET(self):
        fixture = route(self.path)
        if fixture is None:
            self.send_response(404)
            self.end_headers()
            return
        body = (FIX / fixture).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/sendMessage"):
            Handler.sent.append(payload)
            body = json.dumps({"ok": True, "result": {"message_id": len(Handler.sent)}}).encode()
        else:
            body = json.dumps({"ok": False, "description": "unknown method"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def e2e_config(tmp_path, cfg, server, monkeypatch):
    cfg["general"].update({
        "db_path": str(tmp_path / "cars.db"), "cache_dir": str(tmp_path / "cache"),
        "log_dir": str(tmp_path / "logs"), "throttle_seconds": 0, "throttle_jitter_seconds": 0, "max_retries": 1,
    })
    src = cfg["sources"]
    src.update({
        "onemotoring_coe": f"{server}/coe/onemotoring", "sgcarmart_coe_results": f"{server}/coe/sgcarmart",
        "motorist_coe": f"{server}/coe/motorist", "sgcarmart_new_cars": f"{server}/new_cars/",
        "fuel_price": f"{server}/petrol-prices", "fuel_price_fallback": f"{server}/petrol-prices",
        "cnergy": f"{server}/cnergy/", "brand_pages": {"Tesla": f"{server}/brand/tesla"},
        "lta_registrations_by_make_xlsx": f"{server}/lta/m03.xlsx",
    })
    cfg["used"]["searches"]["ev"]["urls"] = {
        "sgcarmart": f"{server}/used_cars/listing.php?FUE=4&PAGE={{page}}",
        "carro": f"{server}/sg/en/buy?fuel=electric&page={{page}}",
        "motorist": f"{server}/used-cars/electric-car?page={{page}}",
    }
    cfg["used"]["searches"]["ice"]["urls"] = {
        "sgcarmart": f"{server}/used_cars/listing.php?FUE=1&PAGE={{page}}",
        "carro": f"{server}/sg/en/buy?fuel=petrol&page={{page}}",
        "motorist": f"{server}/used-cars?page={{page}}",
    }
    cfg["used"]["max_list_pages"] = 1
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    BaseScraper._throttle = None
    BaseScraper._robots = {}
    # Fixture pages carry a few absolute links to the real sites. Treat every host except the
    # local server as disallowed so the test never leaves the machine.
    real_allowed = BaseScraper._allowed

    def local_only(self, url):
        return url.startswith(server) and real_allowed(self, url)

    monkeypatch.setattr(BaseScraper, "_allowed", local_only)
    chromium = "/opt/pw-browsers/chromium"
    if os.path.exists(chromium):
        monkeypatch.setenv("CHROMIUM_EXECUTABLE_PATH", chromium)
    else:
        from scrapers.used_carro import CarroUsedScraper

        monkeypatch.setattr(CarroUsedScraper, "needs_js", False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("TELEGRAM_THREAD_ID", "7")
    monkeypatch.setenv("TELEGRAM_API_BASE", server)
    # No PDF fixture for LTA table M03 exists, so the brand totals it would give are supplied here.
    import pipeline

    monkeypatch.setattr(pipeline, "scrape_registrations", lambda *a, **k: (
        {"BYD": {"total": 9028, "ev": 8318, "petrol": 710}, "TOYOTA": {"total": 4412, "ev": 245, "petrol": 4165},
         "TESLA": {"total": 3723, "ev": 3723, "petrol": 0}}, ["2026-01", "2026-08"]))
    Handler.sent.clear()
    return path


def test_full_run_sends_every_section_to_the_mock_bot_api(e2e_config, capsys):
    assert main.main(["--config", str(e2e_config)]) == 0
    texts = [m["text"] for m in Handler.sent]
    assert len(texts) >= 6
    assert all(len(t) <= MAX_MESSAGE_LENGTH for t in texts)
    assert all(m["chat_id"] == "42" and m["message_thread_id"] == 7 and m["parse_mode"] == "HTML" for m in Handler.sent)
    joined = "\n".join(texts)
    for title in ("SG car market daily", "COE position", "Best Selling Top EV", "Best Selling Used EV", "Best Selling Used Petrol Car", "Cost of ownership"):
        assert title in joined, title
    assert "Best Value list" not in joined
    assert "#1 EV brand · 8,318 new this year" in joined  # the used BYD Atto 3
    assert "#1 petrol brand · 4,165 new this year" in joined  # the used Toyota Corolla Altis
    # The Tesla sedan count comes from the spreadsheet, the Model 3 price from the model page.
    assert "#1 EV sedan brand · 660 registered" in joined
    # Real data flowed through: the COE fixture, the Tesla model page, the used listings and Cnergy.
    assert "131,890" in joined
    assert "Tesla Model 3 RWD 110" in joined
    assert "BYD Atto 3" in joined and "Toyota Corolla Altis" in joined
    assert "Cnergy today" in joined
    assert "first report" in joined
    for t in texts:
        assert t.count("<pre>") == t.count("</pre>")
        assert t.count("<a href=") == t.count("</a>")
    # Every car list is numbered cards, the car name is the link.
    assert re.search(r'^<b>1\. <a href="http', joined, flags=re.M)

    # Same day again: idempotent, nothing resent.
    before = len(Handler.sent)
    assert main.main(["--config", str(e2e_config)]) == 0
    assert len(Handler.sent) == before
    assert "already sent today" in capsys.readouterr().out

    # --force resends even though nothing changed.
    assert main.main(["--config", str(e2e_config), "--force"]) == 0
    assert len(Handler.sent) > before


def test_dry_run_reports_no_change_after_a_send(e2e_config, capsys):
    assert main.main(["--config", str(e2e_config)]) == 0
    from db import Database
    from settings import load_config

    cfg = load_config(e2e_config)
    db = Database(cfg["general"]["db_path"])
    db.conn.execute("UPDATE runs SET sent_at = NULL")  # pretend a new day: only the signature matters
    db.conn.commit()
    db.close()
    assert main.main(["--config", str(e2e_config), "--dry-run"]) == 0
    assert "would not send, nothing changed" in capsys.readouterr().out
