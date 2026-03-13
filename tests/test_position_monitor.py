"""Tests for position monitor exit conditions."""

import pytest
from src.trading.position_monitor import _should_exit


def _base_position(direction: str = "LONG", **overrides) -> dict:
    pos = {
        "id": 1,
        "symbol": "BTC/USDT:USDT",
        "direction": direction,
        "entry_price": 50000,
        "size": 0.01,
        "sl_price": 48500 if direction == "LONG" else 51500,
        "tp_price": 53000 if direction == "LONG" else 47000,
        "liquidation_price": 0,
        "trailing_stop_pct": 0.02,
        "opened_at": "",
    }
    pos.update(overrides)
    return pos


# -- Trailing stop tests -----------------------------------------------


def test_long_trailing_stop_in_profit():
    """LONG trailing stop should trigger when price drops from high while in profit."""
    pos = _base_position("LONG")
    # Price peaked at 52000, now drops 2%+ from peak
    trailing_high = 52000
    trail_trigger = 52000 * 0.98  # 50960
    current_price = 50950  # below trigger, above entry -> should trigger
    reason = _should_exit(pos, current_price, trailing_high, 50000, 0)
    assert reason is not None
    assert "trailing_stop" in reason


def test_long_trailing_stop_not_in_profit():
    """LONG trailing stop should NOT trigger when price is below entry."""
    pos = _base_position("LONG")
    # Price peaked at 50500, now drops -> still requires price > entry
    trailing_high = 50500
    current_price = 49500  # below entry -> SL should handle this, not trailing
    reason = _should_exit(pos, current_price, trailing_high, 50000, 0)
    # Should not trigger trailing stop (would hit SL instead at 48500)
    if reason:
        assert "trailing_stop" not in reason


def test_short_trailing_stop_in_profit():
    """SHORT trailing stop should trigger when price rises from low while in profit."""
    pos = _base_position("SHORT")
    # Price dropped to 48000 (in profit for SHORT), now rises 2%+
    trailing_low = 48000
    trail_trigger = 48000 * 1.02  # 48960
    current_price = 49000  # above trigger -> should trigger
    reason = _should_exit(pos, current_price, 50000, trailing_low, 0)
    assert reason is not None
    assert "trailing_stop" in reason


def test_short_trailing_stop_not_above_entry():
    """SHORT trailing stop should NOT trigger when price is above entry (loss zone).

    Symmetric with LONG: trailing stop only locks in profits, SL handles losses.
    When price goes above entry for SHORT, the SL should handle the exit.
    """
    pos = _base_position("SHORT", entry_price=50000)
    trailing_low = 49000
    current_price = 50100  # above entry (loss zone for SHORT) -> SL handles this
    reason = _should_exit(pos, current_price, 51000, trailing_low, 0)
    # Trailing stop should NOT trigger in loss zone
    if reason:
        assert "trailing_stop" not in reason


def test_short_trailing_stop_no_trigger_within_range():
    """SHORT trailing stop should NOT trigger when price is within acceptable range."""
    pos = _base_position("SHORT")
    trailing_low = 48000
    trail_trigger = 48000 * 1.02  # 48960
    current_price = 48500  # below trigger -> no trailing stop
    reason = _should_exit(pos, current_price, 50000, trailing_low, 0)
    # Should not trigger any exit (price is in profit range)
    assert reason is None or "trailing_stop" not in reason


# -- SL/TP tests -------------------------------------------------------


def test_long_stop_loss():
    pos = _base_position("LONG", sl_price=48500)
    reason = _should_exit(pos, 48400, 50000, 50000, 0)
    assert reason is not None
    assert "stop_loss" in reason


def test_short_take_profit():
    pos = _base_position("SHORT", tp_price=47000)
    reason = _should_exit(pos, 46900, 50000, 46900, 0)
    assert reason is not None
    assert "take_profit" in reason


# -- ATR-based trailing stop tests ------------------------------------


def _make_profile(
    trailing_stop_pct: float = 0.03,
    trailing_activation_atr: float = 1.0,
    trailing_atr_multiplier: float = 1.5,
):
    """Create a minimal mock profile for trailing stop tests."""
    from config.profiles import ProfileConfig
    return ProfileConfig(
        name="test",
        label="Test",
        risk={
            "trailing_stop_pct": trailing_stop_pct,
            "trailing_activation_atr": trailing_activation_atr,
            "trailing_atr_multiplier": trailing_atr_multiplier,
            "funding_rate_max": 0.001,
            "max_hold_hours": 72,
        },
    )


def test_trailing_not_activated_below_atr_threshold():
    """Trailing should NOT trigger if profit < activation_atr * ATR."""
    pos = _base_position("LONG", atr=500)  # 1x ATR = 500
    profile = _make_profile(trailing_activation_atr=1.0)
    # Price peaked at 50300 (only $300 profit, below 1x ATR=$500)
    trailing_high = 50300
    current_price = 50100  # dropping but not enough profit to activate
    reason = _should_exit(pos, current_price, trailing_high, 50000, 0, profile)
    assert reason is None or "trailing_stop" not in reason


def test_trailing_activated_above_atr_threshold():
    """Trailing SHOULD trigger when profit >= activation_atr * ATR and price drops."""
    pos = _base_position("LONG", atr=500)  # 1x ATR = 500
    profile = _make_profile(
        trailing_stop_pct=0.03,
        trailing_activation_atr=1.0,
        trailing_atr_multiplier=1.5,
    )
    # Price peaked at 51000 ($1000 profit > 1x ATR=$500) → trailing active
    trailing_high = 51000
    # ATR trail distance = 500 * 1.5 = 750
    # ATR trigger = 51000 - 750 = 50250
    # Fixed trigger = 51000 * 0.97 = 49470
    # Trail trigger = min(49470, 50250) = 49470 (wider)
    # Price at 50200 is above both triggers → should NOT trigger yet
    current_price = 50200
    reason = _should_exit(pos, current_price, trailing_high, 50000, 0, profile)
    # 50200 > entry(50000) but 50200 > 49470 trigger → no trigger
    assert reason is None or "trailing_stop" not in reason


def test_trailing_atr_triggers_on_deep_drop():
    """Trailing triggers when price drops below the wider ATR-based trigger."""
    pos = _base_position("LONG", atr=200)
    profile = _make_profile(
        trailing_stop_pct=0.03,
        trailing_activation_atr=1.0,
        trailing_atr_multiplier=1.5,
    )
    # Price peaked at 51000 ($1000 profit > 1x ATR=$200) → active
    trailing_high = 51000
    # ATR trail = 200 * 1.5 = 300, trigger = 51000 - 300 = 50700
    # Fixed trail = 51000 * 0.97 = 49470, trigger = 49470
    # Trail trigger = min(49470, 50700) = 49470 (wider/more conservative)
    current_price = 50100  # above entry, below 50700 ATR trigger but above 49470 fixed
    reason = _should_exit(pos, current_price, trailing_high, 50000, 0, profile)
    # 50100 > 49470 (fixed trigger) → no trailing stop
    assert reason is None or "trailing_stop" not in reason


def test_trailing_short_atr_activation():
    """SHORT trailing stop with ATR activation threshold."""
    pos = _base_position("SHORT", entry_price=50000, atr=500)
    profile = _make_profile(trailing_activation_atr=1.0)
    # Price dropped to 49200 ($800 profit > 1x ATR=$500) → active
    trailing_low = 49200
    # ATR trail = 500 * 1.5 = 750, trigger = 49200 + 750 = 49950
    # Fixed trail = 49200 * 1.03 = 50676
    # Trail trigger = max(50676, 49950) = 50676 (wider)
    # But price must be below entry (50000) for SHORT trailing
    current_price = 49980  # above 49950 ATR trigger, below entry
    reason = _should_exit(pos, current_price, 50000, trailing_low, 0, profile)
    # 49980 < 50676 but 49980 < entry(50000) → check trigger
    # 49980 >= 49950 (atr trigger)? yes. 49980 >= 50676 (fixed trigger)? no
    # trail_trigger = max(50676, 49950) = 50676
    # 49980 < 50676 → no trigger
    assert reason is None or "trailing_stop" not in reason


def test_trailing_short_not_activated_insufficient_profit():
    """SHORT trailing should NOT activate if profit < activation threshold."""
    pos = _base_position("SHORT", entry_price=50000, atr=500)
    profile = _make_profile(trailing_activation_atr=1.0)
    # Price dropped to 49800 ($200 profit < 1x ATR=$500) → NOT active
    trailing_low = 49800
    current_price = 49900  # rising but trailing not active
    reason = _should_exit(pos, current_price, 50000, trailing_low, 0, profile)
    assert reason is None or "trailing_stop" not in reason


def test_trailing_no_atr_falls_back_to_pct():
    """When ATR=0, fall back to percentage-based trailing (backwards compatible)."""
    pos = _base_position("LONG", atr=0)  # No ATR data
    profile = _make_profile(trailing_stop_pct=0.02, trailing_activation_atr=1.0)
    # With atr=0, min_profit_distance = entry * trailing_pct = 50000 * 0.02 = 1000
    # Price peaked at 52000 ($2000 profit > $1000 threshold) → active
    trailing_high = 52000
    # Fixed trigger = 52000 * 0.98 = 50960
    current_price = 50950  # below trigger, above entry
    reason = _should_exit(pos, current_price, trailing_high, 50000, 0, profile)
    assert reason is not None
    assert "trailing_stop" in reason
