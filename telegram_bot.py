"""Telegram delivery and the HTML formatting helpers that keep messages phone friendly.

Car lists are cards: a bold numbered link per car with short lines under it. Small tables
(pump prices, cost of ownership) stay in narrow <pre> blocks that fit a phone in portrait.
Method notes go in collapsed quotes so the numbers are read first.
"""
from __future__ import annotations

import html
import logging
import re
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


def card(n: int, title: str, url: str, lines: Iterable[str], tag: str = "") -> str:
    """One car as a bold numbered link followed by short lines. Wide tables wrap on a phone,
    cards do not, and the link sits on the car itself instead of a separate list."""
    head = f'<b>{n}. <a href="{html.escape(url, quote=True)}">{escape(title)}</a></b>'
    if tag:
        head += f" <i>{escape(tag)}</i>"
    return "\n".join([head] + [l for l in lines if l])


def note(text: str) -> str:
    """Collapsed quote for the method notes, so the numbers come first. Text is HTML already."""
    return f"<blockquote expandable>{text}</blockquote>" if text else ""


def build_section(title: str, body_parts: Iterable[str]) -> str:
    """Join a bold title and the body parts with blank lines, skipping empty parts."""
    parts = [f"<b>{escape(title)}</b>"] + [p for p in body_parts if p]
    return "\n\n".join(parts)


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

    def send_message(self, text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[dict]:
        """Send text, splitting into several messages if it is over the limit."""
        results = []
        for chunk in split_message(text, limit):
            payload = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": self.parse_mode,
                "disable_web_page_preview": self.disable_preview,
            }
            if self.thread_id:
                payload["message_thread_id"] = self.thread_id
            results.append(self._post("sendMessage", payload))
            time.sleep(0.5)
        return results

    def send_many(self, messages: Iterable[str]) -> int:
        sent = 0
        for m in messages:
            sent += len(self.send_message(m))
        return sent

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        payload = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        return self._post("getUpdates", payload)
