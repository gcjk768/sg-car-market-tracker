from datetime import date

from bot_listener import parse_update
from report import SECTION_TITLES, coe_section
from telegram_bot import REPORT_BUTTONS, TelegramClient, card

CHAT = "-100"


def test_tags_and_titles_carry_emoji():
    assert "🆕 <i>NEW</i>" in card(1, "BYD Atto 3", "https://x", ["a"], "NEW")
    assert "🟢 <i>DROP ▼2,000</i>" in card(1, "BYD Atto 3", "https://x", ["a"], "DROP ▼2,000")
    assert SECTION_TITLES["coe"].startswith("🎫 ")


def test_coe_falls_green_rises_red():
    rows = [{"category": "A", "premium": 100, "delta": -5}, {"category": "B", "premium": 100, "delta": 5}]
    html = coe_section(date(2026, 9, 30), "Sep 2026", rows, None).html
    assert "🟢 <b>Cat A</b>" in html and "🔴 <b>Cat B</b>" in html


def test_buttons_go_on_the_last_message_only(monkeypatch):
    sent = []
    c = TelegramClient("t", CHAT)
    monkeypatch.setattr(c, "_post", lambda m, p: sent.append(p) or {})
    monkeypatch.setattr("telegram_bot.time.sleep", lambda s: None)
    c.send_many(["one", "two"], buttons=REPORT_BUTTONS)
    assert "reply_markup" not in sent[0] and sent[1]["reply_markup"] == REPORT_BUTTONS


def test_button_press_becomes_a_command():
    u = {"callback_query": {"id": "q1", "data": "/coe", "message": {"chat": {"id": -100}, "message_thread_id": <THREAD_ID>}}}
    assert parse_update(u, CHAT, 7) == ("/coe", "", "q1")
    assert parse_update(u, CHAT, 8) is None  # another topic
    assert parse_update({"callback_query": {**u["callback_query"], "data": "/rm"}}, CHAT, 7) is None


def test_typed_command_still_parsed():
    u = {"message": {"chat": {"id": -100}, "text": "/ask@bot why so dear", "message_thread_id": <THREAD_ID>}}
    assert parse_update(u, CHAT, 7) == ("/ask", "why so dear", None)
    assert parse_update({"message": {"chat": {"id": 5}, "text": "/run"}}, CHAT, None) is None
