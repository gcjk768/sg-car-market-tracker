"""Telegram delivery and the HTML formatting helpers that keep messages phone friendly.

Car lists are cards: a bold numbered link per car with short lines under it. Small tables
(pump prices, cost of ownership) stay in narrow <pre> blocks that fit a phone in portrait.
Method notes go in collapsed quotes so the numbers are read first.
"""
from __future__ import annotations

import html
import logging
import re
import threading
import time
from typing import Iterable, Sequence

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096
DEFAULT_TABLE_WIDTH = 60
ELLIPSIS = "…"


def escape(text: object) -> str:
    """Escape text for Telegram HTML. None becomes an empty string."""
    if text is None:
        return ""
    return html.escape(str(text), quote=False)


def truncate(text: str, width: int) -> str:
    """Cut text to width characters, ending with an ellipsis when something was removed."""
    text = "" if text is None else str(text)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return ELLIPSIS
    return text[: width - 1] + ELLIPSIS


def fmt_money(value: float | int | None, prefix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{prefix}{int(round(value)):,}"


def fmt_int(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{int(round(value)):,}"


def fmt_delta(value: float | int | None, pct: float | None = None) -> str:
    """Signed change shown with arrows so the sign is obvious on a phone."""
    if value is None:
        return "n/a"
    arrow = "▲" if value > 0 else ("▼" if value < 0 else "•")
    text = f"{arrow}{abs(int(round(value))):,}"
    if pct is not None:
        text += f" ({abs(pct):.2f}%)"
    return text


class Column:
    def __init__(self, header: str, width: int, align: str = "left"):
        self.header = header
        self.width = width
        self.align = align

    def fit(self, value: object) -> str:
        text = truncate("" if value is None else str(value), self.width)
        return text.rjust(self.width) if self.align == "right" else text.ljust(self.width)


def render_table(columns: Sequence[Column], rows: Iterable[Sequence[object]], max_width: int = DEFAULT_TABLE_WIDTH) -> str:
    """Render a fixed width table. Total width never exceeds max_width.

    When the columns are too wide for max_width the widest text column is narrowed first.
    """
    cols = [Column(c.header, c.width, c.align) for c in columns]
    gaps = len(cols) - 1
    while sum(c.width for c in cols) + gaps > max_width:
        widest = max((c for c in cols if c.align == "left"), key=lambda c: c.width, default=None)
        if widest is None or widest.width <= 3:
            widest = max(cols, key=lambda c: c.width)
        if widest.width <= 2:
            break
        widest.width -= 1
    header = " ".join(c.fit(c.header) for c in cols).rstrip()
    rule = "─" * min(max_width, sum(c.width for c in cols) + gaps)
    lines = [header, rule]
    for row in rows:
        cells = list(row) + [""] * (len(cols) - len(row))
        lines.append(" ".join(c.fit(v) for c, v in zip(cols, cells)).rstrip())
    return "\n".join(lines)


def pre_block(text: str) -> str:
    return f"<pre>{escape(text)}</pre>"


def link_list(items: Iterable[tuple[str, str]], start: int = 1) -> str:
    """Numbered list of <a href> links. Numbers match the table rows."""
    lines = []
    for n, (label, url) in enumerate(items, start=start):
        lines.append(f'{n}. <a href="{html.escape(url, quote=True)}">{escape(label)}</a>')
    return "\n".join(lines)


def dot(*bits: object) -> str:
    """Escape and join the non empty bits with a middle dot, one short card line."""
    return " · ".join(escape(b) for b in bits if b)


TAG_EMOJI = {"NEW": "🆕", "DROP": "🟢"}

# Buttons under the last message of a report. bot_listener.py runs the command in callback_data.
REPORT_BUTTONS = {"inline_keyboard": [[{"text": "🔄 Run again", "callback_data": "/run"},
                                       {"text": "🎫 COE only", "callback_data": "/coe"}]]}


DIVIDER = "━━━━━━━━━━━━━━━━"


def card(n: int, title: str, url: str, lines: Iterable[str], tag: str = "", emoji: str = "", desc: str = "") -> str:
    """One item block: `emoji <b>n. linked name</b> · description`, then short detail lines that
    the caller leads with an emoji. Wide tables wrap on a phone, blocks do not."""
    head = (f"{emoji} " if emoji else "") + f'<b>{n}. <a href="{html.escape(url, quote=True)}">{escape(title)}</a></b>'
    if desc:
        head += f" · {escape(desc)}"
    if tag:
        mark = TAG_EMOJI.get(tag.split()[0], "")
        head += f" {mark} <i>{escape(tag)}</i>" if mark else f" <i>{escape(tag)}</i>"
    return "\n".join([head] + [l for l in lines if l])


def note(text: str) -> str:
    """Collapsed quote for method notes and other background. build_section moves it to the end."""
    return f"<blockquote expandable>{text}</blockquote>" if text else ""


def header(title: str, subtitle: str = "") -> str:
    """`emoji <b>TITLE</b> · subtitle`. The title's first word is its fixed emoji (report.SECTION_TITLES)."""
    emoji, _, name = title.partition(" ")
    if not name or emoji.isascii():
        emoji, name = "", title
    head = (f"{emoji} " if emoji else "") + f"<b>{escape(name.upper())}</b>"
    return head + (f" · {escape(subtitle)}" if subtitle else "")


def build_section(title: str, body_parts: Iterable[str], subtitle: str = "") -> str:
    """Header, then the body parts with blank lines between, skipping empty parts.
    Collapsed notes go last, so the numbers are read first."""
    parts = [p for p in body_parts if p]
    notes = [p for p in parts if p.startswith("<blockquote")]
    return "\n\n".join([header(title, subtitle)] + [p for p in parts if p not in notes] + notes)


_TAG_RE = re.compile(r"<a href=\"([^\"]+)\">(.*?)</a>|<[^>]+>")


def html_to_text(text: str) -> str:
    """Plain text for the console and for the resend when Telegram rejects the HTML. Links keep their URL."""
    def repl(m: re.Match) -> str:
        return f"{m.group(2)} <{html.unescape(m.group(1))}>" if m.group(1) else ""

    return html.unescape(_TAG_RE.sub(repl, text))


_PRE_OPEN = re.compile(r"<pre>")
_PRE_CLOSE = re.compile(r"</pre>")


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split HTML text into chunks under the Telegram limit.

    Splits on blank lines first so a car card is never cut in half. A paragraph that is itself
    over the limit falls back to line splitting, which keeps <pre> blocks balanced.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buffer = ""
    for para in text.split("\n\n"):
        candidate = para if not buffer else buffer + "\n\n" + para
        if len(candidate) <= limit:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
        if len(para) <= limit:
            buffer = para
        else:
            *full, buffer = _split_lines(para, limit)
            chunks.extend(full)
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if c.strip()]


def _split_lines(text: str, limit: int) -> list[str]:
    """Split on line breaks. A <pre> block that is cut gets closed at the end of one chunk and
    reopened at the start of the next so both chunks stay valid HTML."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buffer = ""
    in_pre = False
    for line in text.split("\n"):
        while len(line) > limit - 12:
            # A single line longer than the limit is cut hard.
            head, line = line[: limit - 12], line[limit - 12 :]
            chunks.extend(_split_lines(_wrap(head, in_pre), limit))
        candidate = line if not buffer else buffer + "\n" + line
        reserve = len("</pre>") if in_pre else 0
        if len(candidate) + reserve > limit:
            chunks.append(buffer + ("</pre>" if in_pre else ""))
            buffer = ("<pre>" if in_pre else "") + line
        else:
            buffer = candidate
        opens = len(_PRE_OPEN.findall(line))
        closes = len(_PRE_CLOSE.findall(line))
        if opens > closes:
            in_pre = True
        elif closes > opens:
            in_pre = False
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if c.strip()]


def _wrap(text: str, in_pre: bool) -> str:
    return f"<pre>{text}</pre>" if in_pre else text


class TelegramError(Exception):
    pass


class _Typing:
    """Context manager behind TelegramClient.typing(). Best effort: a failed refresh is ignored."""

    def __init__(self, client: "TelegramClient", every: float = 4.0):
        self.client, self.every = client, every
        self._stop = threading.Event()

    def _loop(self) -> None:
        payload = {"chat_id": self.client.chat_id, "action": "typing"}
        if self.client.thread_id:
            payload["message_thread_id"] = self.client.thread_id
        while not self._stop.is_set():
            try:
                self.client.http.post(f"{self.client.base}/sendChatAction", json=payload, timeout=5)
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self.every)

    def __enter__(self):
        threading.Thread(target=self._loop, name="typing", daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()



class TelegramClient:
    """Thin Bot API client. Only sendMessage is needed for the report."""

    def __init__(self, token: str, chat_id: str, parse_mode: str = "HTML", disable_preview: bool = True, timeout: float = 30, api_base: str | None = None, thread_id: str | None = None):
        if not token or not chat_id:
            raise TelegramError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        api_base = (api_base or "https://api.telegram.org").rstrip("/")
        self.base = f"{api_base}/bot{token}"
        self.chat_id = chat_id
        self.thread_id = int(thread_id) if thread_id else None
        self.parse_mode = parse_mode
        self.disable_preview = disable_preview
        self.http = httpx.Client(timeout=timeout)

    def typing(self, every: float = 4.0):
        """`with client.typing():` keeps "typing..." showing in the chat while a slow reply is prepared."""
        return _Typing(self, every)

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.TransportError),
        reraise=True,
    )
    def _post(self, method: str, payload: dict) -> dict:
        resp = self.http.post(f"{self.base}/{method}", json=payload)
        if resp.status_code == 429:
            retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
            log.warning("telegram rate limit, sleeping %ss", retry_after)
            time.sleep(retry_after)
            resp = self.http.post(f"{self.base}/{method}", json=payload)
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"{method} failed: {data.get('description', resp.text)}")
        return data["result"]

    def send_message(self, text: str, limit: int = MAX_MESSAGE_LENGTH, buttons: dict | None = None) -> list[dict]:
        """Send text, splitting into several messages if it is over the limit. Buttons go on the last one."""
        results = []
        chunks = split_message(text, limit)
        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": self.parse_mode,
                "disable_web_page_preview": self.disable_preview,
            }
            if self.thread_id:
                payload["message_thread_id"] = self.thread_id
            if buttons and i == len(chunks) - 1:
                payload["reply_markup"] = buttons
            try:
                results.append(self._post("sendMessage", payload))
            except TelegramError as exc:
                if "parse entities" not in str(exc):
                    raise
                # Bad HTML must not lose the report: resend this chunk as plain text.
                log.warning("telegram rejected the HTML, resending as plain text: %s", exc)
                payload["text"] = html_to_text(chunk)[:limit]
                payload.pop("parse_mode")
                results.append(self._post("sendMessage", payload))
            time.sleep(0.5)
        return results

    def send_many(self, messages: Iterable[str], buttons: dict | None = None) -> int:
        messages = list(messages)
        sent = 0
        for i, m in enumerate(messages):
            sent += len(self.send_message(m, buttons=buttons if i == len(messages) - 1 else None))
        return sent

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Stop the button's loading spinner. Telegram expects this within a few seconds of the press."""
        self._post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        payload = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        return self._post("getUpdates", payload)
