"""Put the brands' official prices on the website's new car variants, Sgcarmart's price kept as the fallback.

Official rows (`scrapers/official`, stored in `official_prices`) and Sgcarmart variants name a car differently:
"Sealion 7" "Dynamic" against "Sealion 7" "Dynamic 71.8kWh(A)", or "Kia EV5" "Earth" against "EV5 Electric"
"Earth 64 kWh". A variant takes an official price when:

* the make matches and the model family matches (the same key `website._model_key` uses, or one a whole word
  prefix of the other; when official families are prefixes of the variant's, only the longest one counts),
* the COE category matches when the official row names one (BYD prints a row per category and no trim names),
* and an official price lies within `official.price_tolerance_pct` of Sgcarmart's. Sgcarmart copies the distributors'
  lists, so the same car is priced the same or nearly so, while trims of one model are usually further apart. The
  closest such price wins; when two official prices are both close, the variant's words decide, and if they cannot,
  nothing is guessed and Sgcarmart's price stays.

A row the page prices without COE (Porsche) gets today's premium for the car's COE category added, and the card
says so: the brand's price plus our COE figure, not a price the brand printed.
"""
from __future__ import annotations

import re
from typing import Any, Optional

_WORD = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
NOISE = {"a", "the", "with", "and", "cat", "coe", "electric", "ev", "edition", "new", "auto", "at", "variant"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower().replace("kwh", " kwh ")) if w not in NOISE}


_CAT = re.compile(r"\bcat(?:egory)?\s*([ab])\b", re.I)


def _row_cat(r: dict[str, Any]) -> Optional[str]:
    """Cat A or B when the official row names it (BYD prints one row per COE category, no trim names)."""
    m = _CAT.search(f"{r.get('variant', '')} {r.get('note', '')}")
    return m.group(1).upper() if m else None


def _cat(v: Any) -> Optional[str]:
    c = getattr(v, "coe_category", None)
    return c.value if c is not None and hasattr(c, "value") else c


def _family(make: str, model: str) -> str:
    from website import _model_key

    return _model_key(make, model)


def _same_family(a: str, b: str) -> bool:
    return a == b or a.startswith(b + " ") or b.startswith(a + " ")


def match(variants: list, officials: list[dict[str, Any]], cfg: dict[str, Any], premiums: Optional[dict[str, int]] = None,
          cat_of: Optional[Any] = None) -> dict[int, dict[str, Any]]:
    """{id(variant): official row} for each variant that takes an official price. `variants` are NewEvVariant or
    NewCarVariant objects (make, model, variant, price_with_coe).

    A row the brand prices without COE (Porsche) is compared, and returned, as its price plus today's premium for the
    variant's COE category (`premiums`, {"A": n, "B": n}; `cat_of(variant)` gives the category). The returned copy
    keeps `price_ex_coe`, `coe_added` and `coe_cat` so the card can say so. Without a premium or a category the row
    is not used."""
    tol = float(cfg.get("official", {}).get("price_tolerance_pct", 5)) / 100
    premiums = premiums or {}
    cat_of = cat_of or _cat
    rows = [r for r in officials if r.get("price")]
    by_family: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_family.setdefault(_family(r["make"], r["model"]), []).append(r)
    groups: dict[str, list] = {}
    for v in variants:
        groups.setdefault(_family(v.make, v.model), []).append(v)
    out: dict[int, dict[str, Any]] = {}
    taken: dict[int, tuple] = {}   # id(official row) -> (rank, id(variant)) of the variant that holds it
    for fam, vs in groups.items():
        # Only the closest official family competes: "Octavia Combi RS" takes the "Octavia Combi" rows, not the
        # "Octavia" ones too, else a variant name both share ("Selection 1.5 TSI") ties and nothing matches.
        keys = [k for k in by_family if _same_family(fam, k)]
        near = [k for k in keys if fam == k or fam.startswith(k + " ")]
        keys = [max(near, key=len)] if near else keys
        cands = [r for k in keys for r in by_family[k]
                 if _words(r["make"]) & _words(vs[0].make) or r["make"].lower() == vs[0].make.lower()]
        if not cands:
            continue
        for v in vs:
            if not v.price_with_coe:
                continue
            cat = cat_of(v)

            def eff(r: dict[str, Any]) -> Optional[int]:
                """The row's price with COE: as printed, or plus today's premium when the brand prints it without."""
                if r.get("includes_coe") != "without":
                    return r["price"]
                return r["price"] + premiums[cat] if cat and premiums.get(cat) else None

            pool = [r for r in cands if (not _row_cat(r) or not cat or _row_cat(r) == cat) and eff(r)]
            near = [r for r in pool if abs(eff(r) - v.price_with_coe) <= tol * v.price_with_coe]
            if not near:
                continue
            if len({eff(r) for r in near}) > 1:
                # Two official prices both close: the variant's words decide, else nothing is guessed.
                vw = _words(f"{v.model} {v.variant}")
                scored = sorted(((len(vw & _words(f"{r['model']} {r['variant']}")), -abs(eff(r) - v.price_with_coe), r) for r in near),
                                key=lambda t: (t[0], t[1]), reverse=True)
                if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
                    continue   # same words and same distance: no way to tell which trim this is
                near = [scored[0][2]]
            row = min(near, key=lambda r: abs(eff(r) - v.price_with_coe))
            # One official row prices one variant: the closest one, ties broken by shared words. Nissan lists one
            # Serena price; Sgcarmart has three Serenas within 5 percent of it, and only one of them is that car.
            key = id(row)
            rank = (abs(eff(row) - v.price_with_coe), -len(_words(f"{v.model} {v.variant}") & _words(f"{row['model']} {row['variant']}")))
            if key not in taken or rank < taken[key][0]:
                if key in taken:
                    out.pop(taken[key][1], None)
                taken[key] = (rank, id(v))
                out[id(v)] = row if row.get("includes_coe") != "without" else \
                    {**row, "price": eff(row), "price_ex_coe": row["price"], "coe_added": premiums[cat], "coe_cat": cat}
    return out


def apply(variants: list, officials: list[dict[str, Any]], cfg: dict[str, Any], premiums: Optional[dict[str, int]] = None,
          cat_of: Optional[Any] = None) -> dict[int, dict[str, Any]]:
    """Match, then set each matched variant's price_with_coe to the official price. Returns
    {id(variant): {"official": row, "sgcarmart_price": old price}} for the cards."""
    if not cfg.get("official", {}).get("enabled", True):
        return {}
    found = match(variants, officials, cfg, premiums, cat_of)
    info: dict[int, dict[str, Any]] = {}
    for v in variants:
        row = found.get(id(v))
        if row is None:
            continue
        info[id(v)] = {"official": row, "sgcarmart_price": v.price_with_coe}
        v.price_with_coe = int(row["price"])
    return info


def without_coe_ids(evs: list, cars: list) -> set[int]:
    """Variants whose Sgcarmart price leaves COE out. The full listing says so per variant (withCoe false: Porsche,
    Ferrari, Maserati, Lotus and others); the EV price list does not, so an EV list variant counts when the same make
    has a flagged variant at exactly its price (Porsche's Cayenne Electric at $372,788 is on both)."""
    ids = {id(v) for v in cars if getattr(v, "price_includes_coe", True) is False}
    flagged = {(v.make.lower(), v.price_with_coe) for v in cars if id(v) in ids}
    ids |= {id(v) for v in evs if (v.make.lower(), v.price_with_coe) in flagged}
    return ids


def add_coe(variants: list, ids: set[int], premiums: dict[str, int], cat_of: Any) -> dict[int, dict[str, Any]]:
    """Put today's COE premium into each price that leaves it out, so every card compares like with like. Returns
    {id(variant): {"price_ex_coe", "coe_added", "coe_cat"}}; a variant whose category or premium is unknown is left
    as it is and listed with coe_added None, so the card can say its price excludes COE."""
    out: dict[int, dict[str, Any]] = {}
    for v in variants:
        if id(v) not in ids or not v.price_with_coe:
            continue
        cat = cat_of(v)
        prem = premiums.get(cat) if cat else None
        out[id(v)] = {"price_ex_coe": v.price_with_coe, "coe_added": prem, "coe_cat": cat}
        if prem:
            v.price_with_coe = v.price_with_coe + prem
    return out


def card_fields(entry: Optional[dict[str, Any]], coe: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """What a card shows about where its price came from, and any COE we added to it."""
    if not entry:
        return {"price_source": "sgcarmart", **(coe or {})}
    r = entry["official"]
    if r.get("coe_added"):   # printed without COE: its list price and packages would sit beside a price with COE
        r = {**r, "list_price": None, "packages": {}}
    return {"price_source": "official", "official_url": r.get("source_url") or None, "sgcarmart_price": entry["sgcarmart_price"],
            "list_price": r.get("list_price"), "packages": r.get("packages") or {}, "official_note": r.get("note") or "",
            "official_variant": r.get("variant"), "price_ex_coe": r.get("price_ex_coe"), "coe_added": r.get("coe_added"),
            "coe_cat": r.get("coe_cat")}
