from settings import load_config, user_agent


def test_config_has_required_sections(cfg):
    for key in ("general", "telegram", "sources", "coe", "new_ev", "used", "costs", "buying_considerations"):
        assert key in cfg


def test_section_order_matches_known_keys(cfg):
    assert cfg["telegram"]["section_order"] == ["summary", "coe", "new_ev", "used_ev", "used_ice", "top_sellers", "motorbikes", "fuel", "costs"]


def test_used_filters_are_sane(cfg):
    f = cfg["used"]["filters"]
    assert f["price_ceiling_sgd"] > 0
    assert f["max_owners"] >= 1
    assert f["max_age_years"]["ev"] < f["max_age_years"]["ice"]


def test_user_agent_contains_contact(cfg, monkeypatch):
    monkeypatch.setenv("SCRAPER_CONTACT", "me@example.com")
    assert "me@example.com" in user_agent(cfg)
