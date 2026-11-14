from datetime import date

from past_so2 import config


def test_days_include_padding():
    days = config.days("sundhnukur-2024-05", "met")
    assert days[0] == date(2024, 5, 29)
    assert days[-1] == date(2024, 6, 25)
    assert len(days) == 28


def test_all_eruptions_parse():
    cfg = config.load()
    for name in cfg["eruptions"]:
        e = config.eruption(name, cfg)
        assert e.start < e.end
        assert config.domain(cfg).contains(e.vent_lat, e.vent_lon)
