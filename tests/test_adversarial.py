"""Tests for adversarial signal validation (Bull/Bear debate)."""

import pytest

from src.strategy.adversarial import validate_signal, AdversarialResult


def _make_details(**overrides: str) -> dict:
    """Build indicator vote details with defaults (all LONG)."""
    base = {
        "rsi": {"direction": "LONG", "reason": "oversold (25.0)"},
        "macd": {"direction": "LONG", "reason": "bullish crossover"},
        "ema_trend": {"direction": "LONG", "reason": "above 200 EMA (+2.5%)"},
        "bollinger": {"direction": "NEUTRAL", "reason": "mid-band (0.50)"},
        "ema_cross": {"direction": "LONG", "reason": "golden cross (9 > 21)"},
        "stochastic": {"direction": "LONG", "reason": "oversold crossover (K=18)"},
        "volume": {"direction": "LONG", "reason": "high volume bullish (1.5x avg)"},
        "adx": {"direction": "LONG", "reason": "strong uptrend (ADX=30)"},
    }
    for k, v in overrides.items():
        if k in base:
            base[k]["direction"] = v
    return base


class TestAdversarialValidation:
    def test_long_signal_no_opposition(self):
        """LONG signal with no bearish evidence should pass with zero penalty."""
        result = validate_signal(
            direction="LONG",
            details=_make_details(),
            rsi=25.0,
            adx=30.0,
        )
        assert result.passed is True
        assert result.penalty == 0.0
        assert result.bear_count == 0

    def test_long_signal_minor_opposition(self):
        """LONG signal with 1-2 opposing → small penalty, still passes."""
        result = validate_signal(
            direction="LONG",
            details=_make_details(rsi="SHORT", stochastic="SHORT"),
            rsi=65.0,
            adx=30.0,
        )
        assert result.passed is True
        assert result.bear_count == 2
        assert 0.05 <= result.penalty <= 0.20

    def test_long_signal_strong_opposition_rejected(self):
        """LONG signal with 4+ bearish indicators should be rejected."""
        result = validate_signal(
            direction="LONG",
            details=_make_details(
                rsi="SHORT", macd="SHORT", ema_trend="SHORT", bollinger="SHORT",
            ),
            rsi=75.0,
            adx=30.0,
        )
        assert result.passed is False
        assert result.bear_count == 4

    def test_short_signal_with_bull_opposition(self):
        """SHORT signal checked against bullish evidence."""
        details = _make_details()
        # Flip most to SHORT, keep 2 as LONG (opposing)
        for k in ("macd", "ema_trend", "bollinger", "ema_cross", "stochastic", "volume"):
            details[k]["direction"] = "SHORT"
        # rsi and adx stay LONG
        result = validate_signal(
            direction="SHORT",
            details=details,
            rsi=25.0,
            adx=30.0,
        )
        assert result.passed is True
        assert result.bull_count == 2
        assert result.penalty > 0

    def test_neutral_signal_always_passes(self):
        """NEUTRAL signal should always pass (no trade = no risk)."""
        result = validate_signal(
            direction="NEUTRAL",
            details={},
            rsi=50.0,
            adx=10.0,
        )
        assert result.passed is True
        assert result.penalty == 0.0

    def test_rsi_extreme_adds_penalty_for_long(self):
        """RSI > 75 while LONG adds extra penalty."""
        result = validate_signal(
            direction="LONG",
            details=_make_details(rsi="SHORT", stochastic="SHORT"),
            rsi=80.0,
            adx=30.0,
        )
        # 2 opposing * 0.05 + RSI extreme 0.10 = 0.20
        assert result.penalty >= 0.20

    def test_rsi_extreme_adds_penalty_for_short(self):
        """RSI < 25 while SHORT adds extra penalty."""
        details = _make_details()
        for k in details:
            details[k]["direction"] = "SHORT"
        details["rsi"]["direction"] = "LONG"
        details["macd"]["direction"] = "LONG"

        result = validate_signal(
            direction="SHORT",
            details=details,
            rsi=20.0,
            adx=30.0,
        )
        assert result.penalty >= 0.20  # 2 opposing + RSI extreme

    def test_weak_trend_reduces_penalty(self):
        """Low ADX (weak trend) should reduce opposition impact."""
        # Same opposition, different ADX
        details = _make_details(rsi="SHORT", macd="SHORT")

        strong_trend = validate_signal(
            direction="LONG", details=details, rsi=65.0, adx=30.0,
        )
        weak_trend = validate_signal(
            direction="LONG", details=details, rsi=65.0, adx=10.0,
        )
        assert weak_trend.penalty < strong_trend.penalty

    def test_penalty_capped_at_one(self):
        """Penalty should never exceed 1.0."""
        details = _make_details()
        for k in details:
            details[k]["direction"] = "SHORT"

        result = validate_signal(
            direction="LONG", details=details, rsi=80.0, adx=50.0,
        )
        assert result.penalty <= 1.0

    def test_result_is_frozen(self):
        """AdversarialResult should be immutable."""
        result = validate_signal(direction="NEUTRAL", details={})
        with pytest.raises(AttributeError):
            result.passed = False  # type: ignore[misc]

    def test_reasons_populated(self):
        """Opposing reasons should be captured."""
        result = validate_signal(
            direction="LONG",
            details=_make_details(macd="SHORT"),
            rsi=50.0,
            adx=25.0,
        )
        assert len(result.reasons) == 1
        assert "macd" in result.reasons[0]

    def test_exactly_three_opposing_passes(self):
        """3 opposing should still pass (threshold is 4)."""
        result = validate_signal(
            direction="LONG",
            details=_make_details(rsi="SHORT", macd="SHORT", bollinger="SHORT"),
            rsi=65.0,
            adx=30.0,
        )
        assert result.passed is True
        assert result.bear_count == 3
