"""Obsidian vault on the NAS: the tracker's movement log and memory.

WRITE: one line per event in Activity/YYYY-MM-DD.md (`- HH:MM emoji **what** · detail · [[note]]`,
SGT), one note per tracked used listing in Cars/ and one per COE category in COE/, each with an
append only `## History`.

READ: memory() returns a capped excerpt, newest first, of the relevant car notes and the recent
Activity, which ai.py adds to the analyst note and /ask prompts.

Off unless VAULT_DIR is set. Every public function is best effort: it logs and returns a default
on any error, so the vault can never crash a run or lose a report. No secrets or prompts are
written here.
"""
from __future__ import annotations

import functools
import logging
import os
import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

log = logging.getLogger(__name__)

SGT = ZoneInfo("Asia/Singapore")
MEMORY_CHARS = 4000
MEMORY_DAYS = 7
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]\n\r]+')

HOME = """---
tags: [active]
updated: {today}
---
# SG Car Market

Movement log and memory of the SG car tracker (Telegram topic "SG EV Car Tracker"). The bot writes
here and reads it back before each AI note and /ask answer. Edit freely; History sections are
append only.

* `Activity/`: one note per day, one line per event (runs, new and gone listings, price moves,
  COE results, reports, commands, self repairs).
* `Cars/`: one note per tracked used listing, `Make Model year — source id`, with its price History.
* `COE/`: one note per category with the bidding History.
"""


def safe(default: Any = None):
    """Run the wrapped vault call best effort: log any error and return `default`."""
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                log.warning("vault: %s failed", fn.__name__, exc_info=True)
                return default
        return wrap
    return deco


def root() -> Path | None:
    d = os.getenv("VAULT_DIR")
    return Path(d) if d else None


def enabled() -> bool:
    return root() is not None


def now_sgt() -> datetime:
    return datetime.now(SGT)


def _clean(text: Any) -> str:
    return " ".join(str(text).split())


def _perms(path: Path, top: Path) -> None:
    """Keep files editable by the owner: 664 and dirs 775, owned like the vault folder when we are root."""
    try:
        path.chmod(0o775 if path.is_dir() else 0o664)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            st = top.stat()
            os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        pass


def _ensure(top: Path) -> None:
    top.mkdir(parents=True, exist_ok=True)
    for d in ("Activity", "Cars", "COE"):
        p = top / d
        if not p.exists():
            p.mkdir()
            _perms(p, top)
    home = top / "Home.md"
    if not home.exists():
        _write(home, HOME.format(today=now_sgt().date().isoformat()), top)


def _write(path: Path, text: str, top: Path) -> None:
    """Atomic write: temp file then rename, so Obsidian never sees half a note."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    _perms(path, top)


def _read(path: Path) -> tuple[dict, str]:
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        head, sep, body = text[4:].partition("\n---\n")
        if sep:
            return yaml.safe_load(head) or {}, body
    return {}, text


def _render(fm: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n" + body.rstrip() + "\n"


# Activity


def activity_line(emoji: str, what: str, detail: str = "", link: str | None = None, when: datetime | None = None) -> str:
    when = when or now_sgt()
    parts = [f"{when:%H:%M} {emoji} **{_clean(what)}**"]
    if detail:
        parts.append(_clean(detail))
    if link:
        parts.append(f"[[{link}]]")
    return "- " + " · ".join(parts)


@safe()
def event(emoji: str, what: str, detail: str = "", link: str | None = None, when: datetime | None = None) -> None:
    """Append one line to today's Activity note."""
    top = root()
    if top is None:
        return
    when = when or now_sgt()
    _ensure(top)
    path = top / "Activity" / f"{when:%Y-%m-%d}.md"
    new = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if new:
            fh.write(f"---\ntags: [active]\nupdated: {when:%Y-%m-%d}\n---\n# Activity {when:%Y-%m-%d}\n\n[[Home]]\n\n")
        fh.write(activity_line(emoji, what, detail, link, when) + "\n")
    if new:
        _perms(path, top)


# Cars


def money(n: float | None) -> str:
    return f"${n:,.0f}" if n is not None else "n/a"


def car_title(listing) -> str:
    year = listing.year or (listing.reg_date.year if listing.reg_date else "")
    name = " ".join(str(p) for p in (listing.make, listing.model, year) if p)
    return _clean(_UNSAFE.sub(" ", f"{name} — {listing.source} {listing.listing_id}"))


def _car_path(top: Path, listing) -> Path:
    """The listing's existing note (found by its id, so a renamed model keeps its note), else a new path."""
    suffix = _clean(_UNSAFE.sub(" ", f" — {listing.source} {listing.listing_id}")) + ".md"
    cars = top / "Cars"
    if cars.exists():
        for p in cars.iterdir():
            if p.name.endswith(suffix):
                return p
    return cars / f"{car_title(listing)}.md"


def _days_on_market(listing, today: date) -> int | None:
    return (today - listing.first_seen).days if listing.first_seen else None


def _append_history(path: Path, fm: dict, body: str, line: str, top: Path) -> None:
    if "## History" not in body:
        body = body.rstrip() + "\n\n## History\n"
    _write(path, _render(fm, body.rstrip() + "\n" + line), top)


@safe()
def track_car(listing, today: date) -> None:
    """Create or update the listing's note. History gets a line only when something moved."""
    top = root()
    if top is None:
        return
    _ensure(top)
    path = _car_path(top, listing)
    fm, body = _read(path)
    name = path.stem
    if not body:
        body = (f"# {listing.display_name}\n\n🌐 [listing]({listing.url}) · {listing.source} · "
                f"first seen {listing.first_seen or today} · [[Home]]\n\n## History\n")
        if listing.first_seen == today:
            line = f"- {today} 🆕 listed at {money(listing.price)}"
            event("🆕", "new listing", f"{listing.display_name} {money(listing.price)}", name)
        else:
            line = f"- {today} 📌 tracking at {money(listing.price)}, first seen {listing.first_seen}"
    elif fm.get("price") != listing.price:
        old = fm.get("price")
        delta = listing.price - old if isinstance(old, int) else 0
        emoji, what = ("🟢", "price drop") if delta < 0 else ("🔴", "price rise")
        line = f"- {today} {emoji} price {money(listing.price)} ({'+' if delta > 0 else '-'}{money(abs(delta))})"
        event(emoji, what, f"{listing.display_name} {money(old)} to {money(listing.price)}", name)
    else:
        return
    fm.update({"tags": ["active"], "updated": today.isoformat(), "source": listing.source,
               "listing_id": str(listing.listing_id), "price": listing.price, "url": listing.url,
               "first_seen": (listing.first_seen or today).isoformat(),
               "reported_price": fm.get("reported_price")})
    _append_history(path, fm, body, line, top)


@safe()
def track_cars(listings: Iterable, today: date) -> None:
    for listing in listings:
        track_car(listing, today)


@safe()
def cars_gone(db, source: str, today: date) -> None:
    """Call before db.mark_gone: notes the active listings of `source` that were not seen today."""
    top = root()
    if top is None:
        return
    for listing in db.active_listings():
        if listing.source != source or not listing.last_seen or listing.last_seen >= today:
            continue
        _ensure(top)
        path = _car_path(top, listing)
        days = _days_on_market(listing, today)
        detail = f"{listing.display_name}, last {money(listing.price)}" + (f", {days} days on market" if days is not None else "")
        if path.exists():
            fm, body = _read(path)
            fm.update({"tags": ["archived"], "updated": today.isoformat()})
            _append_history(path, fm, body, f"- {today} ❌ gone, last {money(listing.price)}"
                            + (f", {days} days on market" if days is not None else ""), top)
            event("❌", "listing gone", detail, path.stem)
        else:
            event("❌", "listing gone", detail)


@safe(default=False)
def already_reported(listing) -> bool:
    """True when the vault shows this listing was reported at its current price."""
    top = root()
    if top is None:
        return False
    fm, _ = _read(_car_path(top, listing))
    return fm.get("reported_price") == listing.price


@safe(default=0)
def cars_reported(listings: Iterable, today: date) -> int:
    """Mark listings sent to Telegram. Only a first report or a new price is logged, never a repeat."""
    top = root()
    if top is None:
        return 0
    n = 0
    for listing in listings:
        path = _car_path(top, listing)
        fm, body = _read(path)
        if not body or fm.get("reported_price") == listing.price:
            continue
        again = fm.get("reported_price") is not None
        fm.update({"reported_price": listing.price, "updated": today.isoformat()})
        _append_history(path, fm, body, f"- {today} 📣 {'reported again' if again else 'reported'} at {money(listing.price)}", top)
        event("📣", "reported again" if again else "reported", f"{listing.display_name} {money(listing.price)}", path.stem)
        n += 1
    return n


# COE


@safe()
def coe_result(result, delta: int | None = None) -> None:
    """Add a tender to its category's History once."""
    top = root()
    if top is None:
        return
    _ensure(top)
    title = f"Cat {result.category.value}"
    path = top / "COE" / f"{title}.md"
    fm, body = _read(path)
    key = f"- {result.tender_date.isoformat()} "
    if key in body:
        return
    if not body:
        body = f"# COE {title}\n\nBidding results, newest last. [[Home]]\n\n## History\n"
    change = f" ({'+' if delta > 0 else '-'}{money(abs(delta))})" if delta else ""
    bids = f", bids {result.bids_received:,} for quota {result.quota:,}" if result.bids_received and result.quota else ""
    fm.update({"tags": ["active"], "updated": result.tender_date.isoformat(), "premium": result.quota_premium})
    _append_history(path, fm, body, f"{key}{result.exercise}: {money(result.quota_premium)}{change}{bids}", top)
    event("🎫", "COE result", f"{title} {money(result.quota_premium)}{change}", title)


# Memory


def _history(path: Path) -> list[str]:
    _, body = _read(path)
    return [ln for ln in body.partition("## History")[2].splitlines() if ln.strip()]


@safe(default=[])
def cars_matching(text: str, limit: int = 5) -> list[Path]:
    """Car notes whose make and model appear in `text` (an /ask question), newest first."""
    top = root()
    if top is None or not (top / "Cars").exists():
        return []
    q = " " + " ".join(re.findall(r"\w+", text.lower())) + " "
    hits = []
    for p in (top / "Cars").glob("*.md"):
        name = p.stem.split(" — ")[0]
        name = re.sub(r"\s\d{4}$", "", name).lower()
        if name and f" {' '.join(re.findall(r'[a-z0-9]+', name))} " in q:
            hits.append(p)
    return sorted(hits, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


@safe(default="")
def memory(cars: Iterable = (), query: str = "", chars: int = MEMORY_CHARS, days: int = MEMORY_DAYS) -> str:
    """Capped excerpt, newest first: the relevant car notes, then recent Activity lines."""
    top = root()
    if top is None or not top.is_dir():
        return ""
    today = now_sgt().date()
    out = [f"Today is {today} (SGT)."]
    paths = [_car_path(top, c) for c in cars] + (cars_matching(query) if query else [])
    seen = set()
    for p in paths:
        if p in seen or not p.exists():
            continue
        seen.add(p)
        fm, _ = _read(p)
        first = fm.get("first_seen")
        on_market = f", first seen {first}" if first else ""
        out.append(f"{p.stem}{on_market}:")
        out += list(reversed(_history(p)))
    act = top / "Activity"
    for i in range(days):
        f = act / f"{today - timedelta(days=i)}.md"
        if f.exists():
            lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
            out.append(f"Activity {f.stem}:")
            out += list(reversed(lines))
    return "\n".join(out)[:chars] if len(out) > 1 else ""
