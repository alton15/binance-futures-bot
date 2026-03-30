"""Adversarial signal validation - checks opposing evidence before entry.

Inspired by TradingAgents' Bull/Bear researcher debate pattern.
For each signal direction, counts and weights indicators voting against it.
Strong opposition → penalty or rejection, preventing false signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Weighted importance of opposing indicators
_OPPOSITION_WEIGHTS: dict[str, float] = {
    "macd": 2.0,
    "rsi": 1.5,
    "ema_trend": 1.5,
    "bollinger": 1.0,
    "ema_cross": 1.0,
    "stochastic": 1.0,
    "volume": 0.5,
    "adx": 0.5,
}

# Thresholds
_REJECT_OPPOSING_COUNT = 4
_PENALTY_PER_OPPOSING = 0.05
_RSI_EXTREME_LONG = 75.0
_RSI_EXTREME_SHORT = 25.0
_RSI_EXTREME_PENALTY = 0.10
_WEAK_TREND_ADX = 15.0


@dataclass(frozen=True)
class AdversarialResult:
    """Result of adversarial validation."""

    passed: bool
    penalty: float
    bear_count: int = 0
    bull_count: int = 0
    reasons: tuple[str, ...] = ()


def validate_signal(
    direction: str,
    details: dict[str, Any],
    rsi: float = 50.0,
    adx: float = 25.0,
) -> AdversarialResult:
    """Validate a signal by checking opposing evidence.

    For LONG signals, counts bearish (SHORT) indicators.
    For SHORT signals, counts bullish (LONG) indicators.

    Returns AdversarialResult with pass/fail and penalty amount.
    """
    if direction == "NEUTRAL":
        return AdversarialResult(passed=True, penalty=0.0)

    opposite = "SHORT" if direction == "LONG" else "LONG"
    opposing_count = 0
    reasons: list[str] = []

    for indicator, vote in details.items():
        if vote.get("direction") == opposite:
            opposing_count += 1
            reasons.append(f"{indicator}: {vote.get('reason', 'opposing')}")

    # Base penalty from opposing count
    penalty = opposing_count * _PENALTY_PER_OPPOSING

    # RSI extreme penalty
    if direction == "LONG" and rsi > _RSI_EXTREME_LONG:
        penalty += _RSI_EXTREME_PENALTY
        reasons.append(f"RSI extreme for LONG ({rsi:.1f})")
    elif direction == "SHORT" and rsi < _RSI_EXTREME_SHORT:
        penalty += _RSI_EXTREME_PENALTY
        reasons.append(f"RSI extreme for SHORT ({rsi:.1f})")

    # Weak trend reduces penalty (opposition less meaningful in choppy market)
    if adx < _WEAK_TREND_ADX:
        trend_factor = adx / _WEAK_TREND_ADX
        penalty *= trend_factor

    penalty = min(1.0, round(penalty, 4))

    # Reject if too many opposing indicators
    passed = opposing_count < _REJECT_OPPOSING_COUNT

    bear_count = opposing_count if direction == "LONG" else 0
    bull_count = opposing_count if direction == "SHORT" else 0

    if not passed:
        logger.info(
            "Adversarial REJECTED %s: %d opposing indicators [%s]",
            direction, opposing_count, ", ".join(reasons),
        )
    elif penalty > 0:
        logger.info(
            "Adversarial penalty for %s: -%.1f%% (%d opposing) [%s]",
            direction, penalty * 100, opposing_count, ", ".join(reasons),
        )

    return AdversarialResult(
        passed=passed,
        penalty=penalty,
        bear_count=bear_count,
        bull_count=bull_count,
        reasons=tuple(reasons),
    )
