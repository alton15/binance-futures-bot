"""Tests for signal quality filters in analyzer."""

import pytest
from config.profiles import ProfileConfig
from src.strategy.analyzer import _apply_quality_filters, _extract_volume_ratio
from src.indicators.signals import Signal


def _make_signal(
    direction: str = "LONG",
    strength: float = 0.70,
    macd_dir: str = "LONG",
    vol_reason: str = "normal volume (1.0x avg)",
    bb_dir: str = "NEUTRAL",
) -> Signal:
    """Create a Signal with controllable vote details."""
    return Signal(
        symbol="TEST/USDT:USDT",
        direction=direction,
        strength=strength,
        confirming_count=5,
        details={
            "macd": {"direction": macd_dir, "weight": 2.0, "reason": "test"},
            "rsi": {"direction": direction, "weight": 1.5, "reason": "test"},
            "ema_trend": {"direction": direction, "weight": 1.5, "reason": "test"},
            "bollinger": {"direction": bb_dir, "weight": 1.0, "reason": "test"},
            "ema_cross": {"direction": direction, "weight": 1.0, "reason": "test"},
            "stochastic": {"direction": direction, "weight": 1.0, "reason": "test"},
            "volume": {"direction": "NEUTRAL", "weight": 1.0, "reason": vol_reason},
            "adx": {"direction": direction, "weight": 1.0, "reason": "test"},
        },
    )


def _conservative_profile() -> ProfileConfig:
    return ProfileConfig(
        name="conservative",
        label="Conservative",
        signals={
            "min_confirming": 5,
            "min_strength": 0.65,
            "macd_opposition_penalty": 1.0,
            "low_volume_threshold": 0.5,
            "low_volume_penalty": 1.0,
            "bb_conflict_penalty": 0.20,
        },
    )


def _neutral_profile() -> ProfileConfig:
    return ProfileConfig(
        name="neutral",
        label="Neutral",
        signals={
            "min_confirming": 4,
            "min_strength": 0.60,
            "macd_opposition_penalty": 0.30,
            "low_volume_threshold": 0.5,
            "low_volume_penalty": 0.15,
            "bb_conflict_penalty": 0.10,
        },
    )


def _aggressive_profile() -> ProfileConfig:
    return ProfileConfig(
        name="aggressive",
        label="Aggressive",
        signals={
            "min_confirming": 4,
            "min_strength": 0.55,
            "macd_opposition_penalty": 0.15,
            "low_volume_threshold": 0.0,
            "low_volume_penalty": 0.0,
            "bb_conflict_penalty": 0.0,
        },
    )


# -- MACD opposition tests ---


def test_conservative_rejects_macd_opposition():
    """Conservative profile should reject signal when MACD opposes."""
    signal = _make_signal(direction="LONG", strength=0.70, macd_dir="SHORT")
    result = _apply_quality_filters(0.70, signal, _conservative_profile())
    assert result == 0.0


def test_neutral_penalizes_macd_opposition():
    """Neutral profile should reduce strength by 30% when MACD opposes."""
    signal = _make_signal(direction="LONG", strength=0.70, macd_dir="SHORT")
    result = _apply_quality_filters(0.70, signal, _neutral_profile())
    assert 0.48 <= result <= 0.50  # 0.70 * 0.70 = 0.49


def test_aggressive_light_macd_penalty():
    """Aggressive profile should only reduce strength by 15%."""
    signal = _make_signal(direction="LONG", strength=0.70, macd_dir="SHORT")
    result = _apply_quality_filters(0.70, signal, _aggressive_profile())
    assert 0.58 <= result <= 0.60  # 0.70 * 0.85 = 0.595


def test_macd_neutral_no_penalty():
    """MACD NEUTRAL should not trigger any penalty."""
    signal = _make_signal(direction="LONG", strength=0.70, macd_dir="NEUTRAL")
    result = _apply_quality_filters(0.70, signal, _conservative_profile())
    assert result == 0.70


def test_macd_same_direction_no_penalty():
    """MACD same direction should not trigger penalty."""
    signal = _make_signal(direction="LONG", strength=0.70, macd_dir="LONG")
    result = _apply_quality_filters(0.70, signal, _conservative_profile())
    assert result == 0.70


# -- Low volume tests ---


def test_conservative_rejects_low_volume():
    """Conservative should reject when volume < 0.5x avg."""
    signal = _make_signal(vol_reason="low volume (0.3x avg)")
    result = _apply_quality_filters(0.70, signal, _conservative_profile())
    assert result == 0.0


def test_neutral_penalizes_low_volume():
    """Neutral should reduce strength by 15% for low volume."""
    signal = _make_signal(vol_reason="low volume (0.3x avg)")
    result = _apply_quality_filters(0.70, signal, _neutral_profile())
    assert 0.58 <= result <= 0.60  # 0.70 * 0.85 = 0.595


def test_aggressive_ignores_low_volume():
    """Aggressive should not penalize low volume (threshold=0)."""
    signal = _make_signal(vol_reason="low volume (0.2x avg)")
    result = _apply_quality_filters(0.70, signal, _aggressive_profile())
    assert result == 0.70


def test_normal_volume_no_penalty():
    """Normal volume should not trigger penalty."""
    signal = _make_signal(vol_reason="normal volume (1.2x avg)")
    result = _apply_quality_filters(0.70, signal, _conservative_profile())
    assert result == 0.70


# -- BB conflict tests ---


def test_bb_conflict_conservative_penalty():
    """BB opposing direction should reduce strength by 20% for conservative."""
    signal = _make_signal(direction="LONG", bb_dir="SHORT")
    result = _apply_quality_filters(0.70, signal, _conservative_profile())
    assert 0.55 <= result <= 0.57  # 0.70 * 0.80 = 0.56


def test_bb_conflict_aggressive_no_penalty():
    """Aggressive should ignore BB conflict."""
    signal = _make_signal(direction="LONG", bb_dir="SHORT")
    result = _apply_quality_filters(0.70, signal, _aggressive_profile())
    assert result == 0.70


# -- Combined penalties ---


def test_multiple_penalties_stack():
    """Multiple penalties should stack multiplicatively."""
    # MACD opposes + BB opposes on neutral profile
    signal = _make_signal(direction="LONG", macd_dir="SHORT", bb_dir="SHORT")
    result = _apply_quality_filters(0.70, signal, _neutral_profile())
    # 0.70 * 0.70 (macd) * 0.90 (bb) = 0.441
    assert 0.43 <= result <= 0.45


# -- Volume ratio extraction ---


def test_extract_volume_ratio_low():
    assert _extract_volume_ratio("low volume (0.3x avg)") == 0.3


def test_extract_volume_ratio_high():
    assert _extract_volume_ratio("high volume bullish (1.9x avg)") == 1.9


def test_extract_volume_ratio_no_match():
    assert _extract_volume_ratio("no data") is None


def test_extract_volume_ratio_normal():
    assert _extract_volume_ratio("normal volume (1.0x avg)") == 1.0
