"""Two notes on every used car for the website: a deal verdict and red flags.

Deal verdict (plain arithmetic, no model): the car's depreciation per year against the median of similar live
listings, the same model family and fuel, registered within `site.deal.year_window` years. Depreciation per
year already allows for price and COE left, so it compares cars of different ages fairly. Needs at least
`min_similar` other cars, else no verdict.

Red flags (Claude CLI, `ai` settings): the seller's description plus the car's own figures go to the model,
which lists concerns a buyer should check (ex-taxi or PHV, as is, warranty ending, mileage far above its
age, accident or flood wording the keyword filter missed). Answers are cached per listing in the
`listing_flags` table and only asked again when the description, price or mileage changes, so a normal day
costs a call or two. With AI off or failing, cards simply carry no flags.
"""
from __future__ import annotations

import hashlib
import json
import logging
import statistics
from typing import Any, Optional

log = logging.getLogger(__name__)

FLAGS_PROMPT = (
    "You check used car listings in Singapore for a buyer. Standard input is a JSON list of cars, each with an id, "
    "the car's name, year, mileage in km, owners, COE years left, price in SGD and the seller's description. "
    "For each car list the concerns a careful buyer should check before viewing: likely ex-taxi or private hire "
    "(very high mileage for its age, or wording), sold as is, warranty missing or ending, accident, flood or "
    "repair wording, battery or engine replaced, short COE, parallel import without agent warranty, or anything "
    "else that lowers its value. Only real concerns stated or strongly implied by the data; marketing words are "
    "not concerns, and a car with none gets an empty list. Each concern at most 8 plain words, no dashes. "
    "Return one JSON object and nothing else, mapping each id to its list of concerns. Do not use any tools."
)


def _family(make: str, model: str) -> str:
    from website import _model_key   # the same model family key the popularity ranking uses

    return _model_key(make, model)


def deal_verdicts(cards: list[dict[str, Any]], cfg: dict[str, Any]) -> None:
    """Adds card["deal"] = {"verdict": "under"|"over"|"in line", "diff": $/yr, "pct", "similar": n, "median"}
    to each used car card that has enough similar cars. Goods vehicles never count as similar."""
    d = cfg.get("site", {}).get("deal", {})
    window = int(d.get("year_window", 1))
    need = int(d.get("min_similar", 3))
    band = float(d.get("band_pct", 5))
    pool = [c for c in cards if c.get("dep_year") and c.get("year") and "commercial vehicle" not in (c.get("flags") or [])]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in pool:
        groups.setdefault((_family(c["make"], c["model"]), c["drivetrain"]), []).append(c)
    for c in pool:
        others = [o for o in groups[(_family(c["make"], c["model"]), c["drivetrain"])]
                  if o is not c and abs(o["year"] - c["year"]) <= window]
        if len(others) < need:
            continue
        median = statistics.median(o["dep_year"] for o in others)
        diff = c["dep_year"] - median
        pct = diff / median * 100 if median else 0.0
        c["deal"] = {"verdict": "under" if pct <= -band else "over" if pct >= band else "in line",
                     "diff": int(round(diff)), "pct": round(pct, 1), "similar": len(others), "median": int(round(median))}


def _fingerprint(card: dict[str, Any]) -> str:
    raw = json.dumps([card.get("description", ""), card.get("price"), card.get("mileage_km")], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _facts(card: dict[str, Any]) -> dict[str, Any]:
    return {"id": card["id"], "car": card["name"], "year": card.get("year"), "mileage_km": card.get("mileage_km"),
            "owners": card.get("owners"), "coe_years_left": card.get("coe_left"), "price": card.get("price"),
            "description": (card.get("description") or "")[:600]}


def _batches(items: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = [[]]
    size = 2
    for it in items:
        n = len(json.dumps(it, ensure_ascii=False)) + 2
        if out[-1] and size + n > max_chars:
            out.append([])
            size = 2
        out[-1].append(it)
        size += n
    return [b for b in out if b]


def _clean(flags: Any) -> list[str]:
    if not isinstance(flags, list):
        return []
    out = []
    for f in flags:
        text = " ".join(str(f).replace("\u2014", ",").replace("\u2013", ",").split())   # the site writes no long dashes
        if text and len(text) <= 80 and text not in out:
            out.append(text)
    return out[:4]


def red_flags(cards: list[dict[str, Any]], db, cfg: dict[str, Any], cli=None) -> dict[str, int]:
    """Adds card["red_flags"] (a list, possibly empty) to every card the model has checked. Returns counts:
    {"cached": n, "asked": n, "calls": n}. Never raises; a failed batch stays unchecked until the next build."""
    stats = {"cached": 0, "asked": 0, "calls": 0}
    f = cfg.get("site", {}).get("red_flags", {})
    if not f.get("enabled", True):
        return stats
    todo = []
    for c in cards:
        fp = _fingerprint(c)
        hit = db.get_listing_flags(c["id"], fp)
        if hit is not None:
            c["red_flags"] = hit
            stats["cached"] += 1
        else:
            todo.append((c, fp))
    if not todo:
        return stats
    if cli is None:
        from ai import ClaudeCli

        cli = ClaudeCli(cfg)
    if not cli.available():
        log.info("red flags: AI unavailable, %d cars left unchecked", len(todo))
        return stats
    by_id = {c["id"]: (c, fp) for c, fp in todo}
    max_calls = int(f.get("max_calls", 8))
    for batch in _batches([_facts(c) for c, _ in todo], int(cfg.get("ai", {}).get("max_input_chars", 12000)) - 500):
        if stats["calls"] >= max_calls:
            log.info("red flags: %d calls used, the rest wait for the next build", max_calls)
            break
        stats["calls"] += 1
        try:
            answer = cli.ask_json(FLAGS_PROMPT, json.dumps(batch, ensure_ascii=False)) or {}
        except Exception as exc:   # best effort: the site still builds
            log.warning("red flags: batch failed: %s", exc)
            continue
        for item in batch:
            if item["id"] not in answer:
                continue   # the model skipped it: ask again next time
            card, fp = by_id[item["id"]]
            card["red_flags"] = _clean(answer[item["id"]])
            db.put_listing_flags(item["id"], fp, card["red_flags"])
            stats["asked"] += 1
    return stats
