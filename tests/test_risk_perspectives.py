"""Tests for multi-perspective risk scoring."""

import pytest

from src.risk.perspectives import (
    evaluate_aggressive,
    evaluate_conservative,
    evaluate_neutral,
    evaluate_multi_perspective,
    PerspectiveScore,
    MultiPerspectiveResult,
)


class TestAggressivePerspective:
    def test_strong_signal_high_score(self):
        result = evaluate_aggressive(
            signal_strength=0.80, adx=30.0, rsi=50.0,
            atr=100.0, close_price=50000.0,
            volatility_24h=0.05, funding_rate=0.0001,
        )
        assert result.score >= 0.80
        assert "strong signal" in result.reasons[0]

    def test_weak_signal_low_score(self):
        result = evaluate_aggressive(
            signal_strength=0.50, adx=10.0, rsi=50.0,
            atr=100.0, close_price=50000.0,
        )
        assert result.score < 0.50

    def test_high_volatility_is_opportunity(self):
        low_vol = evaluate_aggressive(
            signal_strength=0.70, adx=25.0, rsi=50.0,
            atr=100.0, close_price=50000.0, volatility_24h=0.02,
        )
        high_vol = evaluate_aggressive(
            signal_strength=0.70, adx=25.0, rsi=50.0,
            atr=100.0, close_price=50000.0, volatility_24h=0.05,
        )
        assert high_vol.score > low_vol.score

    def test_score_clamped(self):
        result = evaluate_aggressive(
            signal_strength=0.95, adx=40.0, rsi=50.0,
            atr=100.0, close_price=50000.0,
            volatility_24h=0.08, funding_rate=0.0001,
        )
        assert result.score <= 1.0

    def test_result_is_frozen(self):
        result = evaluate_aggressive(signal_strength=0.70, adx=None, rsi=None, atr=None, close_price=None)
        with pytest.raises(AttributeError):
            result.score = 0.5  # type: ignore[misc]


class TestConservativePerspective:
    def test_strong_signal_low_volatility(self):
        result = evaluate_conservative(
            signal_strength=0.75, adx=20.0, rsi=50.0,
            atr=100.0, close_price=50000.0,
            volatility_24h=0.02, funding_rate=0.0001,
        )
        assert result.score >= 0.60

    def test_high_volatility_penalized(self):
        result = evaluate_conservative(
            signal_strength=0.70, adx=25.0, rsi=50.0,
            atr=100.0, close_price=50000.0,
            volatility_24h=0.06,
        )
        assert result.score <= 0.50
        assert any("dangerous volatility" in r for r in result.reasons)

    def test_rsi_extreme_for_long(self):
        result = evaluate_conservative(
            signal_strength=0.70, adx=25.0, rsi=72.0,
            atr=100.0, close_price=50000.0,
            direction="LONG",
        )
        assert any("RSI too high" in r for r in result.reasons)

    def test_rsi_extreme_for_short(self):
        result = evaluate_conservative(
            signal_strength=0.70, adx=25.0, rsi=28.0,
            atr=100.0, close_price=50000.0,
            direction="SHORT",
        )
        assert any("RSI too low" in r for r in result.reasons)

    def test_weak_trend_penalized(self):
        result = evaluate_conservative(
            signal_strength=0.70, adx=10.0, rsi=50.0,
            atr=100.0, close_price=50000.0,
        )
        assert any("weak" in r for r in result.reasons)

    def test_high_funding_penalized(self):
        result = evaluate_conservative(
            signal_strength=0.70, adx=25.0, rsi=50.0,
            atr=100.0, close_price=50000.0,
            funding_rate=0.001,
        )
        assert any("funding" in r for r in result.reasons)


class TestNeutralPerspective:
    def test_balanced_score(self):
        result = evaluate_neutral(
            signal_strength=0.65, adx=20.0, rsi=50.0,
            atr=100.0, close_price=50000.0,
            volatility_24h=0.025,
        )
        assert 0.40 <= result.score <= 0.70

    def test_strong_signal_strong_trend(self):
        result = evaluate_neutral(
            signal_strength=0.75, adx=30.0, rsi=50.0,
            atr=100.0, close_price=50000.0,
        )
        assert result.score >= 0.65


class TestMultiPerspective:
    def test_ideal_trade(self):
        """Strong signal, good trend, moderate volatility → high score."""
        result = evaluate_multi_perspective(
            signal_strength=0.80, direction="LONG",
            adx=30.0, rsi=45.0, atr=100.0,
            close_price=50000.0, volatility_24h=0.03,
            funding_rate=0.0001,
        )
        assert result.final_score >= 0.65
        assert result.scale_factor >= 1.0

    def test_poor_trade(self):
        """Weak signal, no trend, high volatility → low score."""
        result = evaluate_multi_perspective(
            signal_strength=0.45, direction="LONG",
            adx=8.0, rsi=72.0, atr=200.0,
            close_price=50000.0, volatility_24h=0.06,
            funding_rate=0.0008,
        )
        assert result.final_score < 0.40
        assert result.scale_factor <= 0.60

    def test_medium_trade(self):
        """Average conditions → normal sizing."""
        result = evaluate_multi_perspective(
            signal_strength=0.65, direction="LONG",
            adx=20.0, rsi=50.0, atr=100.0,
            close_price=50000.0, volatility_24h=0.025,
            funding_rate=0.0002,
        )
        assert 0.50 <= result.final_score <= 0.70
        assert result.scale_factor in (0.80, 1.00, 1.10)

    def test_all_perspectives_populated(self):
        result = evaluate_multi_perspective(
            signal_strength=0.70, direction="LONG",
        )
        assert result.aggressive.name == "aggressive"
        assert result.neutral.name == "neutral"
        assert result.conservative.name == "conservative"

    def test_result_is_frozen(self):
        result = evaluate_multi_perspective(signal_strength=0.70)
        with pytest.raises(AttributeError):
            result.final_score = 0.5  # type: ignore[misc]

    def test_scale_factor_tiers(self):
        """Verify each scale factor tier."""
        # High score → 1.10
        high = evaluate_multi_perspective(
            signal_strength=0.90, adx=35.0, rsi=45.0,
            atr=100.0, close_price=50000.0,
            volatility_24h=0.02, funding_rate=0.0001,
        )
        assert high.scale_factor == 1.10

        # Low score → 0.60
        low = evaluate_multi_perspective(
            signal_strength=0.40, adx=5.0, rsi=75.0,
            atr=200.0, close_price=50000.0,
            volatility_24h=0.07, funding_rate=0.001,
        )
        assert low.scale_factor == 0.60

    def test_none_indicators_handled(self):
        """Should work with None indicator values."""
        result = evaluate_multi_perspective(
            signal_strength=0.70, direction="LONG",
            adx=None, rsi=None, atr=None, close_price=None,
        )
        assert 0.0 <= result.final_score <= 1.0
        assert result.scale_factor > 0
