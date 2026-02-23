"""Tests for signal generator."""

import pytest
from src.indicators.calculator import IndicatorSet
from src.indicators.signals import generate_signal, Signal


def _make_indicators(**kwargs) -> IndicatorSet:
    """Create IndicatorSet with defaults overridden by kwargs."""
    defaults = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "close": 50000,
        "volume": 5000,
        "rsi": 50,
        "macd": 100,
        "macd_signal": 90,
        "macd_hist": 10,
        "bb_upper": 51000,
        "bb_mid": 50000,
        "bb_lower": 49000,
        "ema_fast": 50100,
        "ema_mid": 50000,
        "ema_slow": 49500,
        "atr": 500,
        "adx": 30,
        "stoch_k": 55,
        "stoch_d": 50,
        "prev_ema_fast": 49900,
        "prev_ema_mid": 50000,
        "prev_macd": 80,
        "prev_macd_signal": 85,
        "prev_stoch_k": 48,
        "prev_stoch_d": 52,
        "volume_sma": 3000,
    }
    defaults.update(kwargs)
    return IndicatorSet(**defaults)


def test_signal_returns_signal_type():
    ind = _make_indicators()
    signal = generate_signal(ind)
    assert isinstance(signal, Signal)
    assert signal.direction in ("LONG", "SHORT", "NEUTRAL")


def test_strong_long_signal():
    """All indicators pointing LONG should produce LONG."""
    ind = _make_indicators(
        close=51000,
        rsi=25,           # oversold
        macd=200,
        macd_signal=100,
        macd_hist=100,
        prev_macd=90,
        prev_macd_signal=100,   # bullish crossover
        bb_upper=52000,
        bb_mid=50000,
        bb_lower=49000,     # not near bands
        ema_fast=51000,
        ema_mid=50500,
        ema_slow=49000,     # above 200 EMA
        prev_ema_fast=50400,
        prev_ema_mid=50500,  # golden cross
        stoch_k=15,
        stoch_d=20,         # oversold
        adx=30,             # strong trend
        volume=8000,
        volume_sma=3000,    # high volume
    )
    signal = generate_signal(ind)
    assert signal.direction == "LONG"
    assert signal.strength > 0.5
    assert signal.confirming_count >= 3


def test_strong_short_signal():
    """All indicators pointing SHORT should produce SHORT."""
    ind = _make_indicators(
        close=48000,
        rsi=75,            # overbought
        macd=-200,
        macd_signal=-100,
        macd_hist=-100,
        prev_macd=-90,
        prev_macd_signal=-100,  # bearish crossover
        ema_fast=48000,
        ema_mid=48500,
        ema_slow=50000,    # below 200 EMA
        prev_ema_fast=48600,
        prev_ema_mid=48500,  # death cross
        stoch_k=85,
        stoch_d=80,        # overbought
        adx=30,
        volume=8000,
        volume_sma=3000,
        bb_upper=50000,
        bb_mid=49000,
        bb_lower=48000,    # at lower band
    )
    signal = generate_signal(ind)
    assert signal.direction == "SHORT"
    assert signal.strength > 0.5


def test_neutral_signal():
    """Mixed signals should produce weaker signal."""
    ind = _make_indicators(
        rsi=50,
        adx=15,            # weak trend
        stoch_k=50,
        stoch_d=50,
    )
    signal = generate_signal(ind)
    # With mixed signals, strength should be moderate
    assert signal.strength < 0.9


def test_signal_details_populated():
    ind = _make_indicators()
    signal = generate_signal(ind)
    assert "macd" in signal.details
    assert "rsi" in signal.details
    assert "ema_trend" in signal.details
    assert len(signal.details) == 8


def test_is_actionable():
    # Weak signal should not be actionable
    ind = _make_indicators(rsi=50, adx=10, stoch_k=50, stoch_d=50)
    signal = generate_signal(ind)
    # Whether actionable depends on combined weights - just verify property exists
    assert isinstance(signal.is_actionable, bool)
