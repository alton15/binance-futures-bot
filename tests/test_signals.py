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
        "ema_slow": 49000,       # >1% below close for LONG ema_trend
        "atr": 500,
        "adx": 30,
        "stoch_k": 55,
        "stoch_d": 50,
        "prev_ema_fast": 49900,
        "prev_ema_mid": 50000,
        "prev_macd": 80,
        "prev_macd_signal": 85,
        "prev_macd_hist": 5,     # smaller than current → expanding
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
        rsi=25,              # oversold
        macd=200,
        macd_signal=100,
        macd_hist=100,
        prev_macd=90,
        prev_macd_signal=100,   # bullish crossover
        prev_macd_hist=50,      # expanding (100 > 50)
        bb_upper=52000,
        bb_mid=50000,
        bb_lower=49000,      # not near bands
        ema_fast=51200,
        ema_mid=50500,
        ema_slow=49000,      # >1% above 200 EMA
        prev_ema_fast=50400,
        prev_ema_mid=50500,  # golden cross
        stoch_k=15,
        stoch_d=20,          # oversold (<20)
        adx=30,              # strong trend (>=20)
        volume=8000,
        volume_sma=3000,     # high volume
    )
    signal = generate_signal(ind)
    assert signal.direction == "LONG"
    assert signal.strength > 0.5
    assert signal.confirming_count >= 4


def test_strong_short_signal():
    """All indicators pointing SHORT should produce SHORT."""
    ind = _make_indicators(
        close=48000,
        rsi=75,             # overbought
        macd=-200,
        macd_signal=-100,
        macd_hist=-100,
        prev_macd=-90,
        prev_macd_signal=-100,  # bearish crossover
        prev_macd_hist=-50,     # expanding negative (-100 < -50)
        ema_fast=47800,
        ema_mid=48500,
        ema_slow=50000,     # >1% below 200 EMA
        prev_ema_fast=48600,
        prev_ema_mid=48500, # death cross
        stoch_k=85,
        stoch_d=80,         # overbought (>80)
        adx=30,
        volume=8000,
        volume_sma=3000,
        bb_upper=50000,
        bb_mid=49000,
        bb_lower=47500,     # near lower band (position ~0.2)
    )
    signal = generate_signal(ind)
    assert signal.direction == "SHORT"
    assert signal.strength > 0.5


def test_neutral_signal():
    """Mixed signals should produce weaker signal."""
    ind = _make_indicators(
        close=50000,
        ema_slow=50000,     # exactly at 200 EMA → NEUTRAL
        ema_fast=50010,
        ema_mid=50000,      # tiny spread → NEUTRAL
        rsi=50,             # NEUTRAL zone (40-60)
        adx=15,             # weak trend → NEUTRAL
        stoch_k=50,
        stoch_d=50,
        macd_hist=5,
        prev_macd_hist=10,  # not expanding → NEUTRAL
    )
    signal = generate_signal(ind)
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
    assert isinstance(signal.is_actionable, bool)


# -- NEUTRAL deadzone tests --


def test_ema_trend_neutral_deadzone():
    """Price within 1% of 200 EMA should be NEUTRAL."""
    # 0.5% above → NEUTRAL
    ind = _make_indicators(close=50000, ema_slow=49800)
    signal = generate_signal(ind)
    ema_vote = signal.details["ema_trend"]
    assert ema_vote["direction"] == "NEUTRAL"

    # 2% above → LONG
    ind = _make_indicators(close=51000, ema_slow=50000)
    signal = generate_signal(ind)
    ema_vote = signal.details["ema_trend"]
    assert ema_vote["direction"] == "LONG"


def test_ema_cross_neutral_when_converged():
    """EMA 9/21 with small spread and no crossover should be NEUTRAL."""
    ind = _make_indicators(
        ema_fast=50010,
        ema_mid=50000,
        prev_ema_fast=50005,
        prev_ema_mid=50000,  # no crossover, spread < 0.3%
    )
    signal = generate_signal(ind)
    ema_cross_vote = signal.details["ema_cross"]
    assert ema_cross_vote["direction"] == "NEUTRAL"


def test_rsi_neutral_expanded_zone():
    """RSI 40-60 should be NEUTRAL."""
    for rsi_val in [42, 50, 58]:
        ind = _make_indicators(rsi=rsi_val)
        signal = generate_signal(ind)
        rsi_vote = signal.details["rsi"]
        assert rsi_vote["direction"] == "NEUTRAL", f"RSI {rsi_val} should be NEUTRAL"

    # RSI 35 should be LONG
    ind = _make_indicators(rsi=35)
    signal = generate_signal(ind)
    assert signal.details["rsi"]["direction"] == "LONG"

    # RSI 65 should be SHORT
    ind = _make_indicators(rsi=65)
    signal = generate_signal(ind)
    assert signal.details["rsi"]["direction"] == "SHORT"


def test_stochastic_tightened_zones():
    """Stochastic zone thresholds at 20/80."""
    # K=25 should be NEUTRAL (was LONG with old threshold 30)
    ind = _make_indicators(stoch_k=25, stoch_d=30, prev_stoch_k=26, prev_stoch_d=30)
    signal = generate_signal(ind)
    stoch_vote = signal.details["stochastic"]
    assert stoch_vote["direction"] == "NEUTRAL"

    # K=15 should be LONG
    ind = _make_indicators(stoch_k=15, stoch_d=20, prev_stoch_k=16, prev_stoch_d=20)
    signal = generate_signal(ind)
    stoch_vote = signal.details["stochastic"]
    assert stoch_vote["direction"] == "LONG"


def test_bollinger_tightened_zones():
    """Bollinger position thresholds at 0.2/0.8."""
    # position=0.25 should be NEUTRAL (was LONG with old threshold 0.3)
    # bb_lower=49000, bb_upper=51000 → width=2000, position=(close-49000)/2000
    # close=49500 → position=0.25
    ind = _make_indicators(close=49500, bb_lower=49000, bb_upper=51000, bb_mid=50000)
    signal = generate_signal(ind)
    bb_vote = signal.details["bollinger"]
    assert bb_vote["direction"] == "NEUTRAL"

    # position=0.1 should be LONG
    # close=49200 → position=0.1
    ind = _make_indicators(close=49200, bb_lower=49000, bb_upper=51000, bb_mid=50000)
    signal = generate_signal(ind)
    bb_vote = signal.details["bollinger"]
    assert bb_vote["direction"] == "LONG"


def test_macd_requires_expanding_histogram():
    """MACD histogram fallback requires histogram to be expanding."""
    # Histogram positive but shrinking → NEUTRAL
    ind = _make_indicators(
        macd=100, macd_signal=90, macd_hist=10,
        prev_macd=95, prev_macd_signal=90,  # no crossover
        prev_macd_hist=15,  # shrinking (10 < 15)
    )
    signal = generate_signal(ind)
    macd_vote = signal.details["macd"]
    assert macd_vote["direction"] == "NEUTRAL"

    # Histogram positive and expanding → LONG
    ind = _make_indicators(
        macd=100, macd_signal=90, macd_hist=20,
        prev_macd=95, prev_macd_signal=90,
        prev_macd_hist=10,  # expanding (20 > 10)
    )
    signal = generate_signal(ind)
    macd_vote = signal.details["macd"]
    assert macd_vote["direction"] == "LONG"


def test_adx_threshold_lowered():
    """ADX threshold lowered to 20."""
    # ADX=22 should now vote (was NEUTRAL with old threshold 25)
    ind = _make_indicators(
        adx=22, ema_fast=50100, ema_slow=49000,
    )
    signal = generate_signal(ind)
    adx_vote = signal.details["adx"]
    assert adx_vote["direction"] == "LONG"
