"""Deal verdicts and red flags on used cars (insights.py)."""
import json

import insights
from db import Database


def card(i, dep, year=2023, model="Atto 3", make="BYD", dt="ev", flags=None, desc="", price=100000, km=30000):
    return {"id": f"s-{i}", "make": make, "model": model, "name": f"{make} {model}", "drivetrain": dt, "year": year,
            "dep_year": dep, "flags": flags or [], "description": desc, "price": price, "mileage_km": km,
            "owners": 1, "coe_left": 7.0}


def test_deal_verdict_against_similar_cars(cfg):
    cars = [card(1, 15000), card(2, 17000), card(3, 17500), card(4, 18000),
            card(5, 16000, year=2020),                          # outside the year window: not similar
            card(6, 9000, model="ATTO 3"),                     # another spelling of the same model counts
            card(7, 30000, flags=["commercial vehicle"]),       # a goods vehicle never counts
            card(8, 16000, model="Seal")]                        # another model, alone: no verdict
    insights.deal_verdicts(cars, cfg)
    one = cars[0]["deal"]
    assert one["similar"] == 4 and one["median"] == 17250 and one["verdict"] == "under" and one["diff"] == -2250
    assert cars[3]["deal"]["verdict"] == "over"
    assert "deal" not in cars[4] and "deal" not in cars[6] and "deal" not in cars[7]
    cfg["site"]["deal"]["band_pct"] = 50
    insights.deal_verdicts(cars, cfg)
    assert cars[0]["deal"]["verdict"] == "in line"


class FakeCli:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def available(self):
        return True

    def ask_json(self, prompt, stdin_text=""):
        batch = json.loads(stdin_text)
        self.calls.append([b["id"] for b in batch])
        return {b["id"]: self.answers.get(b["id"], []) for b in batch if b["id"] != "s-skip"}


def test_red_flags_are_cached_until_the_listing_changes(cfg, tmp_path):
    db = Database(tmp_path / "c.db")
    cars = [card(1, 1, desc="Sold as is", km=256000), card(2, 1, desc="Agent serviced"), card("skip", 1)]
    cli = FakeCli({"s-1": ["Sold as is \u2014 no warranty", "256,000 km, likely ex taxi", "x" * 200], "s-2": []})
    stats = insights.red_flags(cars, db, cfg, cli)
    assert stats == {"cached": 0, "asked": 2, "calls": 1}
    assert cars[0]["red_flags"][1:] == ["Sold as is , no warranty", "256,000 km, likely ex taxi"]   # dash gone, overlong dropped
    assert cars[0]["red_flags"][0].startswith("About ") and "ex taxi" in cars[0]["red_flags"][0]   # the mileage rule comes first
    assert cars[1]["red_flags"] == [] and cars[2]["red_flags"] == []   # skipped: rules only, asked again next time

    again = [card(1, 1, desc="Sold as is", km=256000), card(2, 1, desc="Agent serviced", price=95000)]
    stats = insights.red_flags(again, db, cfg, cli)
    assert stats["cached"] == 1 and cli.calls[-1] == ["s-2"]                                   # only the repriced car


def test_red_flags_batches_respect_the_input_cap_and_call_limit(cfg, tmp_path):
    db = Database(tmp_path / "c.db")
    cars = [card(i, 1, desc="y" * 500) for i in range(30)]
    cfg["ai"]["max_input_chars"] = 3000
    cfg["site"]["red_flags"]["max_calls"] = 2
    cli = FakeCli({})
    stats = insights.red_flags(cars, db, cfg, cli)
    assert stats["calls"] == 2 and all(len(json.dumps([insights._facts(c) for c in cars if c["id"] in ids])) <= 2500 for ids in cli.calls)


def test_no_ai_means_no_flags_and_no_error(cfg, tmp_path):
    cars = [card(1, 1)]
    stats = insights.red_flags(cars, Database(tmp_path / "c.db"), cfg)   # tests switch AI off
    assert stats == {"cached": 0, "asked": 0, "calls": 0} and cars[0]["red_flags"] == []   # rules only


def test_rules_see_taxi_mileage_and_classic_plates():
    from datetime import date
    today = date(2026, 10, 17)
    assert insights.km_per_year({"mileage_km": 256000, "reg_date": "2022-10-18"}, today) == 64043
    taxi = insights.rule_flags({"mileage_km": 256000, "reg_date": "2022-10-18", "description": ""}, today)
    assert taxi == ["About 64,043 km a year, likely ex taxi or private hire"]
    lorry = {"mileage_km": 256000, "reg_date": "2022-10-18", "description": "", "flags": ["commercial vehicle"]}
    assert insights.rule_flags(lorry, today) == []
    normal = {"mileage_km": 60000, "reg_date": "2023-01-01", "description": "Agent serviced"}
    assert insights.rule_flags(normal, today) == []
    classic = {"mileage_km": 104518, "year": 2023, "description": "Imported Used Vehicle. Registered under Classic Vehicle Scheme."}
    assert insights.rule_flags(classic, today) == ["Classic or vintage scheme, limited days of use a year"]
    # a 1986 Defender re-registered in 2023: lifetime mileage, not a taxi
    defender = {"mileage_km": 194000, "reg_date": "2023-04-20", "description": "A true classic for vintage car enthusiasts! This 1986 Land Rover"}
    assert insights.rule_flags(defender, today) == ["Built 1986, classic or vintage registration, limited use a year"]
    assert insights.rule_flags({"mileage_km": 30000, "year": 2023, "name": "Mazda 3 Mild Hybrid 1.5A Classic", "description": ""}, today) == []


def test_a_new_prompt_version_asks_again(cfg, tmp_path, monkeypatch):
    db = Database(tmp_path / "c.db")
    cli = FakeCli({})
    insights.red_flags([card(1, 1)], db, cfg, cli)
    monkeypatch.setattr(insights, "PROMPT_VERSION", insights.PROMPT_VERSION + 1)
    insights.red_flags([card(1, 1)], db, cfg, cli)
    assert len(cli.calls) == 2
