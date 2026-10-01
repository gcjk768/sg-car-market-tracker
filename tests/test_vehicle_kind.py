from models import Drivetrain
from scrapers.used_common import detect_drivetrain, is_commercial_model
from settings import load_config


def test_fuel_type_beats_the_search_default():
    # Live 2026-10-01: these came from Motorist's electric car search and landed in the EV list.
    assert detect_drivetrain("Petrol SUZUKI EVERY JOIN AUTO", Drivetrain.ev) == Drivetrain.ice
    assert detect_drivetrain("Petrol SKODA OCTAVIA AMBITION 1.0 TSI MHEV", Drivetrain.ev) == Drivetrain.hybrid
    assert detect_drivetrain("Petrol-Electric Toyota Corolla Cross", Drivetrain.ev) == Drivetrain.hybrid
    assert detect_drivetrain("Electric BYD M3e", Drivetrain.ice) == Drivetrain.ev
    assert detect_drivetrain("BYD Atto 3", Drivetrain.ev) == Drivetrain.ev  # no fuel named, keep the default


def test_goods_vehicles_are_caught_by_model_name():
    models = load_config()["used"]["filters"]["exclude_models"]
    for title in ("Toyota TOWN ACE 1.5GL AUTO", "Toyota DYNA 150 5MT", "Honda N - VAN + STYLE FUN TURBO",
                  "Honda N VAN STYLE FUN TURBO 660 CVT", "Suzuki EVERY JOIN AUTO"):
        assert is_commercial_model(title, models), title
    for title in ("Toyota Raize 1.2A X", "Honda FIT 1.3 BASIC CVT", "BYD e6 Electric", "BYD Dolphin Electric Dynamic",
                  "Toyota YARIS CROSS 1.5G CVT", "Mazda MX-30 EV RC"):
        assert not is_commercial_model(title, models), title


def test_extra_model_urls_are_opened_first(monkeypatch):
    from datetime import date
    from scrapers.new_ev import SgcarmartNewEvScraper

    cfg = load_config()
    cfg["new_ev"]["extra_model_urls"] = ["https://www.sgcarmart.com/new-cars/info/21506/tesla-model-y-electric"]
    s = SgcarmartNewEvScraper(cfg, "test", date(2026, 10, 2))
    opened = []
    monkeypatch.setattr(s, "fetch", lambda url: opened.append(url) or "")
    monkeypatch.setattr(s, "parse", lambda html: [{"slug": "byd", "url": "https://www.sgcarmart.com/new-cars/info/1/byd-seal-electric", "title": "b"}])
    monkeypatch.setattr(s, "parse_model", lambda html, url: [])
    try:
        s.run()
    except Exception:
        pass  # no variants parsed from the stub pages
    assert opened[1:] == ["https://www.sgcarmart.com/new-cars/info/21506/tesla-model-y-electric",
                          "https://www.sgcarmart.com/new-cars/info/1/byd-seal-electric"]
