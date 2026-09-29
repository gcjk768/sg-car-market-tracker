import json
from datetime import date

from db import Database
from models import CoeResult, NewEvVariant, UsedListing
from pipeline import Pipeline, should_send
from settings import load_config


def _pipe(tmp_path, cfg):
    db = Database(tmp_path / "t.db")
    p = Pipeline(cfg, db, date(2026, 9, 29), "ua")
    p.coe_latest = [CoeResult(tender_date=date(2026, 9, 23), exercise="x", category="A", quota_premium=131890, source="t")]
    p.new_evs = [NewEvVariant(make="Tesla", model="Model 3", variant="RWD 110", price_with_coe=179999, range_km=534, listing_url="u", price_source_url="u", source="t")]
    l = UsedListing(source="t", listing_id="1", url="u", make="BYD", model="Atto 3", drivetrain="ev", price=118800)
    p.used_ev = [(l, "NEW")]
    p.used_ice = []
    return db, p


def test_no_changes_means_no_send(tmp_config, tmp_path):
    cfg = load_config(tmp_config)
    db, p = _pipe(tmp_path, cfg)
    send, reasons, sig = should_send(cfg, db, p, date(2026, 9, 29), force=False)
    assert send and reasons == ["first report"]
    db.set_state("last_sent_signature", json.dumps(sig))
    # Next day, same cars, same prices, tag no longer NEW: nothing to say.
    p.used_ev = [(p.used_ev[0][0], "")]
    send, reasons, _ = should_send(cfg, db, p, date(2026, 9, 30), force=False)
    assert send is False and reasons == []
    assert should_send(cfg, db, p, date(2026, 9, 30), force=True)[0] is True


def test_changes_are_described(tmp_config, tmp_path):
    cfg = load_config(tmp_config)
    db, p = _pipe(tmp_path, cfg)
    db.set_state("last_sent_signature", json.dumps(p.signature()))
    p.coe_latest = [CoeResult(tender_date=date(2026, 10, 7), exercise="x", category="A", quota_premium=130000, source="t")]
    p.new_evs[0].price_with_coe = 175999
    p.used_ev[0][0].price = 116800
    p.used_ice = [(UsedListing(source="t", listing_id="9", url="u", make="Toyota", model="Altis", drivetrain="hybrid", price=98800), "NEW")]
    send, reasons, _ = should_send(cfg, db, p, date(2026, 10, 7), force=False)
    assert send is True
    assert reasons == [
        "new COE tender 2026-10-07",
        "new EV list: 1 repriced",
        "used EV shortlist: 1 price drops",
        "used petrol and hybrid shortlist: 1 new",
    ]


def test_heartbeat_and_toggle(tmp_config, tmp_path):
    cfg = load_config(tmp_config)
    db, p = _pipe(tmp_path, cfg)
    db.set_state("last_sent_signature", json.dumps(p.signature()))
    db.start_run(date(2026, 9, 20))
    db.mark_sent(date(2026, 9, 20))
    cfg["telegram"]["heartbeat_after_days"] = 7
    send, reasons, _ = should_send(cfg, db, p, date(2026, 9, 29), force=False)
    assert send is True and "heartbeat" in reasons[0]
    cfg["telegram"]["heartbeat_after_days"] = 0
    assert should_send(cfg, db, p, date(2026, 9, 29), force=False)[0] is False
    cfg["telegram"]["send_only_on_change"] = False
    assert should_send(cfg, db, p, date(2026, 9, 29), force=False)[0] is True
