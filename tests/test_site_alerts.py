"""The website's watchlist and "since your last visit" logic, run in node from the alerts block of site/index.html."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
PAGE = (Path(__file__).parent.parent / "site" / "index.html").read_text(encoding="utf-8")
BLOCK = re.search(r"/\* alerts:begin.*?/\* alerts:end \*/", PAGE, re.S)
MONEY = re.search(r"^const money = .*$", PAGE, re.M)     # pchip in the block formats with the page's money()


def run(js: str):
    """Runs the page's alerts block plus a snippet that ends with an expression; returns that expression as JSON."""
    if not NODE:
        pytest.skip("node is not installed")
    out = subprocess.run([NODE, "-e", MONEY.group(0) + "\n" + BLOCK.group(0) + "\nconsole.log(JSON.stringify((() => {" + js + "})()));"],
                         capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)
    return json.loads(out.stdout)


def data(at, used, new, coe, news=()):
    return {"generated_at": at, "used": used, "new_evs": [v for v in new if v.get("fuel") == "ev"],
            "new_cars": [v for v in new if v.get("fuel") != "ev"], "coe": {"rows": [{"cat": c, "premium": p} for c, p in coe.items()]},
            "news": [{"link": n, "title": n, "published": "2026-10-17"} for n in news]}


def u(id_, price, passes=True):
    return {"id": id_, "price": price, "passes": passes, "name": id_}


def v(model, variant, price, fuel="ev"):
    return {"make": "BYD", "model": model, "variant": variant, "price": price, "fuel": fuel, "id": "new-0"}


OLD = data("2026-10-16T05:00:00",
           [u("sgcarmart-1", 100000), u("motorist-2", 90000), u("motorist-3", 80000, passes=False), u("sgcarmart-9", 70000)],
           [v("Atto 3", "Evo", 180000), v("Atto 3", "Std", 170000), v("Atto 3 Electric", "Base", 150000, fuel="petrol"), v("Seal", "AWD", 200000)],
           {"A": 100000, "B": 120000}, news=["https://lta/a"])
NEW = data("2026-10-17T05:00:00",
           [u("sgcarmart-1", 95000), u("motorist-2", 92000), u("motorist-3", 75000, passes=False), u("sgcarmart-4", 60000),
            u("motorist-5", 50000, passes=False)],
           [v("Atto 3", "Evo", 180000), v("Atto 3", "Std", 165000), v("Atto 3 Electric", "Base", 150000, fuel="petrol")],
           {"A": 98000, "B": 125000}, news=["https://lta/a", "https://lta/b"])


def test_snapshot_keys_survive_card_positions_and_keep_the_cheapest_duplicate():
    snap = run(f"""const d = {json.dumps(NEW)};
        d.new_evs.push({{make: "BYD", model: "Atto 3", variant: "Evo", price: 179000, id: "new-7"}});
        return snapOf(d);""")
    assert snap["at"] == "2026-10-17T05:00:00"
    assert snap["used"]["sgcarmart-1"] == 95000           # source plus listing id, not a card position
    assert snap["new"]["BYD|Atto 3|Evo"] == 179000        # the same variant twice: the cheaper price
    assert snap["coe"] == {"A": 98000, "B": 125000}
    assert snap["news"] == ["https://lta/a", "https://lta/b"]


def test_a_new_build_moves_the_snapshot_to_prev_and_a_reload_keeps_it():
    r = run("""const a = {at: "1", new: {}}, b = {at: "2", new: {}};
        const first = nextSnaps(null, a), second = nextSnaps(first, b), reload = nextSnaps(second, {at: "2", new: {}});
        return [first.prev, second.prev, reload.prev];""")
    assert r == [None, {"at": "1", "new": {}, "held": {}}, {"at": "1", "new": {}, "held": {}}]


def test_a_new_model_the_scrape_skips_for_one_build_is_not_gone():
    """The new EV scrape drops models for a day and brings them back (BMW iX on 2026-10-09). Only two builds in a row is gone."""
    r = run(f"""const d1 = {json.dumps(NEW)}, skip = JSON.parse(JSON.stringify(d1)), back = JSON.parse(JSON.stringify(d1));
        skip.generated_at = "2"; skip.new_evs = skip.new_evs.filter(v => v.model !== "Atto 3");
        back.generated_at = "3";
        const watch = {{"new:BYD|Atto 3": {{}}}};
        const s1 = nextSnaps(null, snapOf(d1)), s2 = nextSnaps(s1, snapOf(skip)), reload = nextSnaps(s2, snapOf(skip)), s3 = nextSnaps(reload, snapOf(back));
        const s2b = nextSnaps(s2, Object.assign(snapOf(skip), {{at: "3"}})), s4 = nextSnaps(s2b, Object.assign(snapOf(skip), {{at: "4"}}));
        return {{skip: changes(s2.prev, s2.cur, skip, watch).gone, reloadPrice: priceIn(reload.cur, "new:BYD|Atto 3"),
                back: changes(s3.prev, s3.cur, back, watch).gone,
                twice: changes(s2b.prev, s2b.cur, skip, watch).gone, thrice: changes(s4.prev, s4.cur, skip, watch).gone}};""")
    assert r["skip"] == [] and r["reloadPrice"] == 165000      # held at its last price, also across a reload
    assert r["back"] == []
    assert r["twice"] == ["new:BYD|Atto 3"]                     # missing from two builds in a row: gone, once
    assert r["thrice"] == []


def test_price_chip_colours_are_for_the_buyer():
    r = run('return [pchip(-5000), pchip(2000), pchip(0)];')
    assert 'chip good' in r[0] and "5,000" in r[0]
    assert 'chip bad' in r[1] and "2,000" in r[1]
    assert 'chip flat' in r[2]


def test_go_to_clears_every_filter_that_could_hide_the_card():
    r = run("""return [filtersFor("used", {drivetrain: "ev", make: "BYD"}), filtersFor("used", {drivetrain: "hybrid", make: "Toyota"}),
        filtersFor("new", {fuel: "ev", make: "BMW"}), filtersFor("new", {fuel: "petrol", make: "Honda"})];""")
    used_ev, used_hybrid, new_ev, new_petrol = r
    assert used_ev["shortlist"] is False and used_ev["q"] == "" and used_ev["kind"] == "ev" and used_ev["make"] == "BYD"
    assert used_hybrid["kind"] == "ice"
    assert all(used_ev[f] == "" for f in ("dt", "body", "budget", "km", "coe", "owners", "src"))
    assert new_ev["kind"] == "ev" and new_ev["q"] == "" and new_ev["make"] == "BMW" and new_petrol["kind"] == "ice"
    assert all(new_ev[f] == "" for f in ("fuel", "body", "budget", "cat"))


def test_a_malformed_stored_snapshot_is_not_compared_against():
    r = run(f"""const good = snapOf({json.dumps(NEW)});
        return [okSnap(good) === good, okSnap(null), okSnap({{at: "1", used: {{}}, new: {{}}, coe: {{}}, news: "x"}}),
                okSnap({{at: "1", used: {{}}, new: {{}}, news: []}}), okSnap("junk")];""")
    assert r == [True, None, None, None, None]


def test_hidden_elements_stay_hidden_over_author_display_rules():
    """.nbadge{display:inline-block} beat the browser's [hidden] rule, so the empty nav badge took up space."""
    assert "[hidden]{display:none!important}" in PAGE


def test_watched_price_is_the_cheapest_variant_of_the_model_or_null_when_gone():
    r = run(f"""const s = snapOf({json.dumps(NEW)});
        return [priceIn(s, "new:BYD|Atto 3"), priceIn(s, "new:BYD|Seal"), priceIn(s, "used:sgcarmart-1"), priceIn(s, "used:sgcarmart-9")];""")
    assert r == [165000, None, 95000, None]     # "Atto 3" never picks up "Atto 3 Electric"


def test_changes_since_last_visit():
    ch = run(f"""const old = {json.dumps(OLD)}, d = {json.dumps(NEW)};
        const watch = {{"used:motorist-3": {{}}, "used:sgcarmart-9": {{}}, "new:BYD|Seal": {{}}}};
        return changes(snapOf(old), snapOf(d), d, watch);""")
    assert [x["id"] for x in ch["fresh"]] == ["sgcarmart-4"]                       # new and shortlisted only
    assert [(m["u"]["id"], m["d"]) for m in ch["drops"]] == [("sgcarmart-1", -5000), ("motorist-3", -5000)]  # watched though off the list
    assert [(m["u"]["id"], m["d"]) for m in ch["rises"]] == [("motorist-2", 2000)]
    assert [(m["key"], m["d"]) for m in ch["newMoves"]] == [("BYD|Atto 3|Std", -5000)]
    assert [(m["cat"], m["d"]) for m in ch["coe"]] == [("A", -2000), ("B", 5000)]
    assert [n["link"] for n in ch["news"]] == ["https://lta/b"]
    assert sorted(ch["gone"]) == ["new:BYD|Seal", "used:sgcarmart-9"]
    assert ch["count"] == 1 + 2 + 1 + 1 + 2 + 1 + 2


def test_first_visit_has_no_changes():
    assert run(f"return changes(null, snapOf({json.dumps(NEW)}), {json.dumps(NEW)}, {{}});") is None
