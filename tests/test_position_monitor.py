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
