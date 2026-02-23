"""Tests for indicator calculator."""

import pytest
import random
from src.indicators.calculator import compute_indicators, IndicatorSet


def _generate_ohlcv(n: int = 250, base_price: float = 50000) -> list[list]:
    """Generate synthetic OHLCV data for testing."""
    data = []
    price = base_price
    ts = 1700000000000
    for i in range(n):
        change = random.uniform(-0.02, 0.02)
        o = price
        c = price * (1 + change)
        h = max(o, c) * (1 + random.uniform(0, 0.01))
        l = min(o, c) * (1 - random.uniform(0, 0.01))
        v = random.uniform(100, 10000)
        data.append([ts + i * 3600000, o, h, l, c, v])
        price = c
    return data


def test_compute_indicators_returns_indicator_set():
    random.seed(42)
    ohlcv = _generate_ohlcv(250)
    result = compute_indicators(ohlcv, "BTC/USDT:USDT", "1h")
    assert result is not None
    assert isinstance(result, IndicatorSet)
    assert result.symbol == "BTC/USDT:USDT"
    assert result.close > 0


def test_compute_indicators_has_all_fields():
    random.seed(42)
    ohlcv = _generate_ohlcv(250)
    result = compute_indicators(ohlcv, "BTC/USDT:USDT", "1h")
    assert result is not None
    assert result.rsi is not None
    assert result.macd is not None
    assert result.macd_signal is not None
    assert result.bb_upper is not None
    assert result.bb_lower is not None
    assert result.ema_fast is not None
    assert result.ema_mid is not None
    assert result.ema_slow is not None
    assert result.atr is not None
    assert result.adx is not None
    assert result.stoch_k is not None
    assert result.stoch_d is not None


def test_compute_indicators_insufficient_data():
    ohlcv = _generate_ohlcv(50)
    result = compute_indicators(ohlcv, "BTC/USDT:USDT", "1h")
    assert result is None


def test_rsi_range():
    random.seed(42)
    ohlcv = _generate_ohlcv(250)
    result = compute_indicators(ohlcv, "BTC/USDT:USDT", "1h")
    assert result is not None
    assert 0 <= result.rsi <= 100


def test_previous_values_populated():
    random.seed(42)
    ohlcv = _generate_ohlcv(250)
    result = compute_indicators(ohlcv, "BTC/USDT:USDT", "1h")
    assert result is not None
    assert result.prev_ema_fast is not None
    assert result.prev_ema_mid is not None
    assert result.prev_macd is not None
