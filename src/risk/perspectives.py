"""Multi-perspective risk scoring - three viewpoints for position sizing.

Inspired by TradingAgents' 3-perspective risk debate
(Aggressive/Conservative/Neutral analysts). Each perspective scores
a trade from its viewpoint, and the weighted average determines
position size adjustment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Perspective weights in final score
_WEIGHTS = {
    "aggressive": 0.25,
    "neutral": 0.50,
    "conservative": 0.25,
}


@dataclass(frozen=True)
class PerspectiveScore:
    """Score from a single risk perspective."""

    name: str
    score: float        # 0.0 (reject) ~ 1.0 (full confidence)
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MultiPerspectiveResult:
    """Combined result from all three perspectives."""

    aggressive: PerspectiveScore
    neutral: PerspectiveScore
    conservative: PerspectiveScore
    final_score: float      # Weighted average 0.0 ~ 1.0
    scale_factor: float     # Position size multiplier (0.5 ~ 1.2)


def evaluate_aggressive(
    signal_strength: float,
    adx: float | None,
    rsi: float | None,
    atr: float | None,
    close_price: float | None,
    volatility_24h: float = 0,
    funding_rate: float = 0,
    direction: str = "LONG",
) -> PerspectiveScore:
    """Aggressive perspective - focuses on reward potential.

    Favors: strong signals, strong trends, high momentum.
    Penalizes: weak signals, low trend strength.
    """
    score = 0.5
    reasons: list[str] = []

    # Signal strength is king
    if signal_strength >= 0.75:
        score += 0.25
        reasons.append(f"strong signal ({signal_strength:.2f})")
    elif signal_strength >= 0.60:
        score += 0.10
        reasons.append(f"decent signal ({signal_strength:.2f})")
    else:
        score -= 0.10
        reasons.append(f"weak signal ({signal_strength:.2f})")

    # Strong trend = opportunity
    if adx is not None and adx >= 25:
        score += 0.15
        reasons.append(f"strong trend (ADX={adx:.0f})")
    elif adx is not None and adx >= 15:
        score += 0.05
        reasons.append(f"moderate trend (ADX={adx:.0f})")

    # Volatility = opportunity (aggressive likes it)
    if volatility_24h >= 0.04:
        score += 0.10
        reasons.append(f"high volatility ({volatility_24h:.1%})")

    # Low funding rate = less drag
    if abs(funding_rate) < 0.0003:
        score += 0.05
        reasons.append("low funding cost")

    return PerspectiveScore(
        name="aggressive",
        score=max(0.0, min(1.0, round(score, 4))),
        reasons=tuple(reasons),
    )


def evaluate_conservative(
    signal_strength: float,
    adx: float | None,
    rsi: float | None,
    atr: float | None,
    close_price: float | None,
    volatility_24h: float = 0,
    funding_rate: float = 0,
    direction: str = "LONG",
) -> PerspectiveScore:
    """Conservative perspective - focuses on risk minimization.

    Favors: confirming indicators, low volatility, good risk/reward.
    Penalizes: high volatility, extreme RSI, high funding.
    """
    score = 0.5
    reasons: list[str] = []

    # Signal must be strong
    if signal_strength >= 0.70:
        score += 0.15
        reasons.append(f"strong confirmation ({signal_strength:.2f})")
    elif signal_strength < 0.55:
        score -= 0.20
        reasons.append(f"insufficient confirmation ({signal_strength:.2f})")

    # Volatility = risk
    if volatility_24h >= 0.05:
        score -= 0.20
        reasons.append(f"dangerous volatility ({volatility_24h:.1%})")
    elif volatility_24h >= 0.03:
        score -= 0.05
        reasons.append(f"moderate volatility ({volatility_24h:.1%})")
    else:
        score += 0.10
        reasons.append(f"safe volatility ({volatility_24h:.1%})")

    # RSI extremes are risky (could reverse)
    if rsi is not None:
        if direction == "LONG" and rsi > 65:
            score -= 0.15
            reasons.append(f"RSI too high for LONG ({rsi:.0f})")
        elif direction == "SHORT" and rsi < 35:
            score -= 0.15
            reasons.append(f"RSI too low for SHORT ({rsi:.0f})")
        elif 40 <= rsi <= 60:
            score += 0.05
            reasons.append(f"RSI neutral zone ({rsi:.0f})")

    # Funding rate cost
    if abs(funding_rate) >= 0.0005:
        score -= 0.15
        reasons.append(f"high funding drag ({funding_rate:.4%})")
    elif abs(funding_rate) >= 0.0003:
        score -= 0.05
        reasons.append(f"moderate funding ({funding_rate:.4%})")

    # Weak trend = choppy, risky
    if adx is not None and adx < 15:
        score -= 0.15
        reasons.append(f"weak/choppy trend (ADX={adx:.0f})")

    return PerspectiveScore(
        name="conservative",
        score=max(0.0, min(1.0, round(score, 4))),
        reasons=tuple(reasons),
    )


def evaluate_neutral(
    signal_strength: float,
    adx: float | None,
    rsi: float | None,
    atr: float | None,
    close_price: float | None,
    volatility_24h: float = 0,
    funding_rate: float = 0,
    direction: str = "LONG",
) -> PerspectiveScore:
    """Neutral perspective - balanced risk/reward assessment.

    Considers both opportunity and risk equally.
    """
    score = 0.5
    reasons: list[str] = []

    # Signal strength (balanced view)
    if signal_strength >= 0.70:
        score += 0.15
        reasons.append(f"good signal ({signal_strength:.2f})")
    elif signal_strength >= 0.55:
        score += 0.05
        reasons.append(f"adequate signal ({signal_strength:.2f})")
    else:
        score -= 0.10
        reasons.append(f"weak signal ({signal_strength:.2f})")

    # Trend (moderate impact)
    if adx is not None:
        if adx >= 25:
            score += 0.10
            reasons.append(f"clear trend (ADX={adx:.0f})")
        elif adx < 15:
            score -= 0.10
            reasons.append(f"no clear trend (ADX={adx:.0f})")

    # Volatility (moderate concern)
    if volatility_24h >= 0.05:
        score -= 0.10
        reasons.append(f"high volatility ({volatility_24h:.1%})")
    elif volatility_24h < 0.015:
        score -= 0.05
        reasons.append(f"low volatility ({volatility_24h:.1%})")

    # Funding (moderate concern)
    if abs(funding_rate) >= 0.0005:
        score -= 0.10
        reasons.append(f"significant funding ({funding_rate:.4%})")

    return PerspectiveScore(
        name="neutral",
        score=max(0.0, min(1.0, round(score, 4))),
        reasons=tuple(reasons),
    )


def evaluate_multi_perspective(
    signal_strength: float,
    direction: str = "LONG",
    adx: float | None = None,
    rsi: float | None = None,
    atr: float | None = None,
    close_price: float | None = None,
    volatility_24h: float = 0,
    funding_rate: float = 0,
) -> MultiPerspectiveResult:
    """Run all three perspectives and produce a weighted score.

    The final score maps to a position size scale factor:
    - score >= 0.70: scale 1.10 (slight increase)
    - score >= 0.55: scale 1.00 (normal)
    - score >= 0.40: scale 0.80 (reduced)
    - score < 0.40:  scale 0.60 (heavily reduced)
    """
    kwargs: dict[str, Any] = {
        "signal_strength": signal_strength,
        "adx": adx,
        "rsi": rsi,
        "atr": atr,
        "close_price": close_price,
        "volatility_24h": volatility_24h,
        "funding_rate": funding_rate,
        "direction": direction,
    }

    agg = evaluate_aggressive(**kwargs)
    con = evaluate_conservative(**kwargs)
    neu = evaluate_neutral(**kwargs)

    final_score = round(
        agg.score * _WEIGHTS["aggressive"]
        + neu.score * _WEIGHTS["neutral"]
        + con.score * _WEIGHTS["conservative"],
        4,
    )

    # Map score to position size scale factor
    if final_score >= 0.70:
        scale_factor = 1.10
    elif final_score >= 0.55:
        scale_factor = 1.00
    elif final_score >= 0.40:
        scale_factor = 0.80
    else:
        scale_factor = 0.60

    logger.info(
        "Multi-perspective: agg=%.2f neu=%.2f con=%.2f → final=%.2f scale=%.2f",
        agg.score, neu.score, con.score, final_score, scale_factor,
    )

    return MultiPerspectiveResult(
        aggressive=agg,
        neutral=neu,
        conservative=con,
        final_score=final_score,
        scale_factor=scale_factor,
    )
