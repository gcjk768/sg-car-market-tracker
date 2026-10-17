from settings import load_config, user_agent


def test_config_has_required_sections(cfg):
    for key in ("general", "site", "sources", "coe", "new_ev", "used", "costs", "buying_considerations"):
        assert key in cfg
    assert "telegram" not in cfg
    assert set(cfg["coe"]["results_watch"]) >= {"enabled", "start", "stop", "poll_seconds"}


def test_used_filters_are_sane(cfg):
    f = cfg["used"]["filters"]
    assert f["price_ceiling_sgd"] > 0
    assert f["max_owners"] >= 1
    assert f["max_age_years"]["ev"] < f["max_age_years"]["ice"]


def test_user_agent_contains_contact(cfg, monkeypatch):
    monkeypatch.setenv("SCRAPER_CONTACT", "me@example.com")
    assert "me@example.com" in user_agent(cfg)
