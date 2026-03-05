"""Tests for trading profiles."""

import pytest
from config.profiles import (
    ProfileConfig,
    CONSERVATIVE,
    NEUTRAL,
    AGGRESSIVE,
    ALL_PROFILES,
    get_profile,
)
from config.settings import RISK, SIGNALS


def test_get_risk_returns_profile_value():
    assert CONSERVATIVE.get_risk("risk_per_trade_pct") == 0.015
    assert NEUTRAL.get_risk("risk_per_trade_pct") == 0.02
    assert AGGRESSIVE.get_risk("risk_per_trade_pct") == 0.03


def test_get_risk_falls_back_to_global():
    # funding_rate_max is NOT overridden in profiles -> falls back to global
    for profile in ALL_PROFILES:
        assert profile.get_risk("funding_rate_max") == RISK["funding_rate_max"]


def test_get_signal_returns_profile_value():
    assert CONSERVATIVE.get_signal("min_confirming") == 5
    assert NEUTRAL.get_signal("min_confirming") == 4
    assert AGGRESSIVE.get_signal("min_confirming") == 4


def test_get_signal_falls_back_to_global():
    # 'weights' is NOT overridden in profiles
    for profile in ALL_PROFILES:
        assert profile.get_signal("weights") == SIGNALS["weights"]


def test_get_profile_valid_names():
    assert get_profile("conservative") is CONSERVATIVE
    assert get_profile("neutral") is NEUTRAL
    assert get_profile("aggressive") is AGGRESSIVE


def test_get_profile_invalid_name():
    with pytest.raises(ValueError, match="Unknown profile 'invalid'"):
        get_profile("invalid")


def test_all_profiles_count():
    assert len(ALL_PROFILES) == 3


def test_conservative_leverage_range():
    assert CONSERVATIVE.leverage_min == 1
    assert CONSERVATIVE.leverage_max == 3


def test_neutral_leverage_range():
    assert NEUTRAL.leverage_min == 2
    assert NEUTRAL.leverage_max == 6


def test_aggressive_leverage_range():
    assert AGGRESSIVE.leverage_min == 3
    assert AGGRESSIVE.leverage_max == 10


def test_leverage_tiers_are_sorted():
    for profile in ALL_PROFILES:
        tiers = profile.get_leverage_tiers()
        for i in range(len(tiers) - 1):
            assert tiers[i]["max_volatility"] < tiers[i + 1]["max_volatility"]


def test_profile_is_frozen():
    with pytest.raises(AttributeError):
        NEUTRAL.name = "modified"


def test_signal_strength_ordering():
    assert CONSERVATIVE.get_risk("signal_strength_min") > NEUTRAL.get_risk("signal_strength_min")
    assert NEUTRAL.get_risk("signal_strength_min") >= AGGRESSIVE.get_risk("signal_strength_min")


def test_risk_per_trade_ordering():
    assert CONSERVATIVE.get_risk("risk_per_trade_pct") < NEUTRAL.get_risk("risk_per_trade_pct")
    assert NEUTRAL.get_risk("risk_per_trade_pct") < AGGRESSIVE.get_risk("risk_per_trade_pct")


def test_max_drawdown_ordering():
    assert CONSERVATIVE.get_risk("max_drawdown_pct") < NEUTRAL.get_risk("max_drawdown_pct")
    assert NEUTRAL.get_risk("max_drawdown_pct") < AGGRESSIVE.get_risk("max_drawdown_pct")


def test_max_margin_per_trade_pct():
    assert CONSERVATIVE.get_risk("max_margin_per_trade_pct") == 0.12
    assert NEUTRAL.get_risk("max_margin_per_trade_pct") == 0.15
    assert AGGRESSIVE.get_risk("max_margin_per_trade_pct") == 0.15
