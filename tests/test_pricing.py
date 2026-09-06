"""Unit tests for the pure pricing logic in ``transformer/pricing.py``."""

from datetime import datetime, timezone

import pytest

from transformer import pricing
from transformer.pricing import PricingError

UTC = timezone.utc


def _dt(y, m, d, h=0, minute=0) -> datetime:
    return datetime(y, m, d, h, minute, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Model normalisation
# --------------------------------------------------------------------------- #

def test_normalize_model_maps_route_to_pricing_model():
    route_map = {"deepseek-chat": "deepseek-v4-flash"}
    assert pricing.normalize_model("deepseek-chat", route_map) == "deepseek-v4-flash"


def test_normalize_model_returns_none_for_unmapped_or_empty():
    route_map = {"deepseek-chat": "deepseek-v4-flash"}
    assert pricing.normalize_model("deepseek-reasoner", route_map) is None
    assert pricing.normalize_model("", route_map) is None
    assert pricing.normalize_model("deepseek-chat", {}) is None


def test_classify_unknown():
    assert pricing.classify_unknown("weird-route") == "weird-route"
    assert pricing.classify_unknown("") == "unknown"


# --------------------------------------------------------------------------- #
# Effort override
# --------------------------------------------------------------------------- #

def test_effort_for_defaults_when_no_override_leaf():
    leaves = {"outputTokens": 5, "session.id": "s1"}
    assert pricing.effort_for(leaves, "low") == "low"


def test_effort_for_overrides_on_leaf_name_at_any_depth():
    assert pricing.effort_for({"effort": "high"}, "low") == "high"
    assert pricing.effort_for({"body.reasoningEffort": " medium "}, "low") == "medium"
    assert pricing.effort_for({"usage.detail.effort": "Low"}, "low") == "low"
    assert pricing.effort_for({"reasoningEffort": 2}, "low") == "2"


def test_effort_for_ignores_empty_override():
    assert pricing.effort_for({"effort": ""}, "low") == "low"


# --------------------------------------------------------------------------- #
# Peak / off-peak
# --------------------------------------------------------------------------- #

def test_period_off_peak_monday_midday_2026_09_07():
    # Mon 2026-09-07 12:00:00Z: hour outside windows -> off_peak (fixture case).
    assert pricing.period_for(_dt(2026, 9, 7, 12)) == "off_peak"


def test_period_peak_inside_window_on_weekday():
    # Monday hour 08 is inside [6,10] -> peak.
    assert pricing.period_for(_dt(2026, 9, 7, 8)) == "peak"
    # Monday hour 03 is inside [1,4] -> peak.
    assert pricing.period_for(_dt(2026, 9, 7, 3)) == "peak"


def test_period_off_peak_on_weekend_even_inside_window():
    # Sat 2026-09-05 08:00 (ISO 6) -> not a peak weekday -> off_peak.
    assert pricing.period_for(_dt(2026, 9, 5, 8)) == "off_peak"


def test_period_for_custom_windows_and_weekdays():
    # Sunday-only, window [1,4]; Sunday 2026-09-06 hour 02 -> peak.
    assert pricing.period_for(_dt(2026, 9, 6, 2), [(1, 4)], [7]) == "peak"
    assert pricing.period_for(_dt(2026, 9, 6, 12), [(1, 4)], [7]) == "off_peak"


# --------------------------------------------------------------------------- #
# Price selection
# --------------------------------------------------------------------------- #

_ROWS = [
    {"model": "m", "slot": "output", "period": "off_peak",
     "price_per_million": 1.0, "effective_from": "2026-08-01T00:00:00Z",
     "pricing_version": "v1"},
    {"model": "m", "slot": "output", "period": "off_peak",
     "price_per_million": 2.0, "effective_from": "2026-09-01T00:00:00Z",
     "pricing_version": "v2"},
]


def test_price_for_picks_most_recent_active_row():
    assert pricing.price_for("m", "output", "off_peak", _dt(2026, 9, 7, 12), _ROWS) == (2.0, "v2")
    assert pricing.price_for("m", "output", "off_peak", _dt(2026, 8, 15), _ROWS) == (1.0, "v1")


def test_price_for_none_when_before_first_row_or_no_match():
    assert pricing.price_for("m", "output", "off_peak", _dt(2026, 7, 1), _ROWS) is None
    assert pricing.price_for("other", "output", "off_peak", _dt(2026, 9, 7), _ROWS) is None
    assert pricing.price_for("m", "input_miss", "off_peak", _dt(2026, 9, 7), _ROWS) is None
    assert pricing.price_for("m", "output", "peak", _dt(2026, 9, 7), _ROWS) is None
    assert pricing.price_for("m", "output", "off_peak", _dt(2026, 9, 7), []) is None


def test_price_for_naive_and_aware_ts_equivalent():
    naive = datetime(2026, 9, 7, 12)
    assert pricing.price_for("m", "output", "off_peak", naive, _ROWS) == (2.0, "v2")


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #

def test_load_pricing_repo_seed(tmp_path):
    repo_config = tmp_path / "pricing.json"
    repo_config.write_text(open("configs/pricing.json", encoding="utf-8").read(), encoding="utf-8")
    cfg = pricing.load_pricing(str(repo_config))
    assert cfg["pricing_version"] == "v1"
    assert cfg["default_effort"] == "low"
    assert cfg["route_map"]["deepseek-chat"] == "deepseek-v4-flash"
    assert len(cfg["rows"]) == 16
    # pricing_version is denormalised into every row.
    assert all(row["pricing_version"] == "v1" for row in cfg["rows"])
    assert all(row["currency"] == "USD" for row in cfg["rows"])
    # Deterministic fixture prices, off-peak flash.
    off_peak = [r for r in cfg["rows"]
                if r["model"] == "deepseek-v4-flash" and r["period"] == "off_peak"]
    assert {r["slot"]: r["price_per_million"] for r in off_peak} == {
        "input_miss": 0.22, "input_hit": 0.007, "input_write": 0.22, "output": 0.66,
    }


def test_load_pricing_missing_file_raises(tmp_path):
    with pytest.raises(PricingError):
        pricing.load_pricing(str(tmp_path / "nope.json"))


def test_load_pricing_invalid_rows_raise(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"pricing_version":"v1","default_effort":"low","route_map":{},'
        '"rows":[{"model":"m","slot":"nope","period":"off_peak",'
        '"price_per_million":1,"effective_from":"2026-08-16T00:00:00Z"}]}',
        encoding="utf-8",
    )
    with pytest.raises(PricingError):
        pricing.load_pricing(str(bad))
