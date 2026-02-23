"""Signal generator - combines indicator votes into trading signals."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from config.settings import SIGNALS
from src.indicators.calculator import IndicatorSet

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """A generated trading signal."""

    symbol: str
    direction: str  # "LONG", "SHORT", or "NEUTRAL"
    strength: float  # 0.0 ~ 1.0
    confirming_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return (
            self.direction != "NEUTRAL"
            and self.confirming_count >= SIGNALS["min_confirming"]
            and self.strength >= SIGNALS["min_strength"]
        )


def generate_signal(indicators: IndicatorSet) -> Signal:
    """Generate a trading signal from indicator set.

    Uses weighted voting across 8 indicator groups:
    - MACD (2.0): crossover + histogram direction
    - RSI (1.5): overbought/oversold + divergence
    - EMA Trend (1.5): price vs 200 EMA
    - Bollinger Bands (1.0): price position in bands
    - EMA Cross (1.0): 9/21 EMA crossover
    - Stochastic (1.0): K/D crossover + levels
    - Volume (1.0): volume confirmation
    - ADX (0.5): trend strength filter

    Returns:
        Signal with direction, strength, and vote details
    """
    weights = SIGNALS["weights"]
    votes: dict[str, dict[str, Any]] = {}
    long_score = 0.0
    short_score = 0.0
    total_weight = 0.0

    # 1. MACD
    direction, reason = _vote_macd(indicators)
    votes["macd"] = {"direction": direction, "weight": weights["macd"], "reason": reason}
    if direction == "LONG":
        long_score += weights["macd"]
    elif direction == "SHORT":
        short_score += weights["macd"]
    total_weight += weights["macd"]

    # 2. RSI
    direction, reason = _vote_rsi(indicators)
    votes["rsi"] = {"direction": direction, "weight": weights["rsi"], "reason": reason}
    if direction == "LONG":
        long_score += weights["rsi"]
    elif direction == "SHORT":
        short_score += weights["rsi"]
    total_weight += weights["rsi"]

    # 3. EMA Trend
    direction, reason = _vote_ema_trend(indicators)
    votes["ema_trend"] = {"direction": direction, "weight": weights["ema_trend"], "reason": reason}
    if direction == "LONG":
        long_score += weights["ema_trend"]
    elif direction == "SHORT":
        short_score += weights["ema_trend"]
    total_weight += weights["ema_trend"]

    # 4. Bollinger Bands
    direction, reason = _vote_bollinger(indicators)
    votes["bollinger"] = {"direction": direction, "weight": weights["bollinger"], "reason": reason}
    if direction == "LONG":
        long_score += weights["bollinger"]
    elif direction == "SHORT":
        short_score += weights["bollinger"]
    total_weight += weights["bollinger"]

    # 5. EMA Cross
    direction, reason = _vote_ema_cross(indicators)
    votes["ema_cross"] = {"direction": direction, "weight": weights["ema_cross"], "reason": reason}
    if direction == "LONG":
        long_score += weights["ema_cross"]
    elif direction == "SHORT":
        short_score += weights["ema_cross"]
    total_weight += weights["ema_cross"]

    # 6. Stochastic
    direction, reason = _vote_stochastic(indicators)
    votes["stochastic"] = {"direction": direction, "weight": weights["stochastic"], "reason": reason}
    if direction == "LONG":
        long_score += weights["stochastic"]
    elif direction == "SHORT":
        short_score += weights["stochastic"]
    total_weight += weights["stochastic"]

    # 7. Volume
    direction, reason = _vote_volume(indicators)
    votes["volume"] = {"direction": direction, "weight": weights["volume"], "reason": reason}
    if direction == "LONG":
        long_score += weights["volume"]
    elif direction == "SHORT":
        short_score += weights["volume"]
    total_weight += weights["volume"]

    # 8. ADX
    direction, reason = _vote_adx(indicators)
    votes["adx"] = {"direction": direction, "weight": weights["adx"], "reason": reason}
    if direction == "LONG":
        long_score += weights["adx"]
    elif direction == "SHORT":
        short_score += weights["adx"]
    total_weight += weights["adx"]

    # Determine final direction and strength
    if total_weight == 0:
        return Signal(symbol=indicators.symbol, direction="NEUTRAL", strength=0, details=votes)

    if long_score > short_score:
        final_direction = "LONG"
        strength = long_score / total_weight
    elif short_score > long_score:
        final_direction = "SHORT"
        strength = short_score / total_weight
    else:
        final_direction = "NEUTRAL"
        strength = 0

    # Count confirming indicators
    confirming = sum(
        1 for v in votes.values()
        if v["direction"] == final_direction
    )

    signal = Signal(
        symbol=indicators.symbol,
        direction=final_direction,
        strength=round(strength, 4),
        confirming_count=confirming,
        details=votes,
    )

    logger.info(
        "Signal %s: %s strength=%.2f confirming=%d actionable=%s",
        indicators.symbol, signal.direction, signal.strength,
        signal.confirming_count, signal.is_actionable,
    )
    return signal


# -- Individual Indicator Votes ------------------------------------


def _vote_macd(ind: IndicatorSet) -> tuple[str, str]:
    """MACD crossover + histogram direction."""
    if ind.macd is None or ind.macd_signal is None:
        return "NEUTRAL", "no data"

    # Bullish crossover: MACD crosses above signal
    if (
        ind.prev_macd is not None
        and ind.prev_macd_signal is not None
        and ind.prev_macd <= ind.prev_macd_signal
        and ind.macd > ind.macd_signal
    ):
        return "LONG", "bullish crossover"

    # Bearish crossover
    if (
        ind.prev_macd is not None
        and ind.prev_macd_signal is not None
        and ind.prev_macd >= ind.prev_macd_signal
        and ind.macd < ind.macd_signal
    ):
        return "SHORT", "bearish crossover"

    # Histogram direction
    if ind.macd_hist is not None:
        if ind.macd > ind.macd_signal and ind.macd_hist > 0:
            return "LONG", "histogram positive"
        elif ind.macd < ind.macd_signal and ind.macd_hist < 0:
            return "SHORT", "histogram negative"

    return "NEUTRAL", "no signal"


def _vote_rsi(ind: IndicatorSet) -> tuple[str, str]:
    """RSI overbought/oversold."""
    if ind.rsi is None:
        return "NEUTRAL", "no data"

    if ind.rsi < 30:
        return "LONG", f"oversold ({ind.rsi:.1f})"
    elif ind.rsi > 70:
        return "SHORT", f"overbought ({ind.rsi:.1f})"
    elif ind.rsi < 45:
        return "LONG", f"below midline ({ind.rsi:.1f})"
    elif ind.rsi > 55:
        return "SHORT", f"above midline ({ind.rsi:.1f})"

    return "NEUTRAL", f"neutral ({ind.rsi:.1f})"


def _vote_ema_trend(ind: IndicatorSet) -> tuple[str, str]:
    """Price position relative to 200 EMA."""
    if ind.ema_slow is None:
        return "NEUTRAL", "no data"

    pct = (ind.close - ind.ema_slow) / ind.ema_slow
    if ind.close > ind.ema_slow:
        return "LONG", f"above 200 EMA ({pct:+.2%})"
    else:
        return "SHORT", f"below 200 EMA ({pct:+.2%})"


def _vote_bollinger(ind: IndicatorSet) -> tuple[str, str]:
    """Price position within Bollinger Bands."""
    if ind.bb_upper is None or ind.bb_lower is None or ind.bb_mid is None:
        return "NEUTRAL", "no data"

    bb_width = ind.bb_upper - ind.bb_lower
    if bb_width == 0:
        return "NEUTRAL", "zero width"

    position = (ind.close - ind.bb_lower) / bb_width

    if ind.close <= ind.bb_lower:
        return "LONG", f"at lower band ({position:.2f})"
    elif ind.close >= ind.bb_upper:
        return "SHORT", f"at upper band ({position:.2f})"
    elif position < 0.3:
        return "LONG", f"near lower band ({position:.2f})"
    elif position > 0.7:
        return "SHORT", f"near upper band ({position:.2f})"

    return "NEUTRAL", f"mid-band ({position:.2f})"


def _vote_ema_cross(ind: IndicatorSet) -> tuple[str, str]:
    """EMA 9/21 crossover."""
    if ind.ema_fast is None or ind.ema_mid is None:
        return "NEUTRAL", "no data"

    # Golden cross
    if (
        ind.prev_ema_fast is not None
        and ind.prev_ema_mid is not None
        and ind.prev_ema_fast <= ind.prev_ema_mid
        and ind.ema_fast > ind.ema_mid
    ):
        return "LONG", "golden cross (9 > 21)"

    # Death cross
    if (
        ind.prev_ema_fast is not None
        and ind.prev_ema_mid is not None
        and ind.prev_ema_fast >= ind.prev_ema_mid
        and ind.ema_fast < ind.ema_mid
    ):
        return "SHORT", "death cross (9 < 21)"

    # Current position
    if ind.ema_fast > ind.ema_mid:
        return "LONG", "9 EMA above 21 EMA"
    else:
        return "SHORT", "9 EMA below 21 EMA"


def _vote_stochastic(ind: IndicatorSet) -> tuple[str, str]:
    """Stochastic K/D crossover + levels."""
    if ind.stoch_k is None or ind.stoch_d is None:
        return "NEUTRAL", "no data"

    # Oversold crossover
    if ind.stoch_k < 20 and ind.stoch_k > ind.stoch_d:
        return "LONG", f"oversold crossover (K={ind.stoch_k:.1f})"

    # Overbought crossover
    if ind.stoch_k > 80 and ind.stoch_k < ind.stoch_d:
        return "SHORT", f"overbought crossover (K={ind.stoch_k:.1f})"

    # Bullish crossover
    if (
        ind.prev_stoch_k is not None
        and ind.prev_stoch_d is not None
        and ind.prev_stoch_k <= ind.prev_stoch_d
        and ind.stoch_k > ind.stoch_d
    ):
        return "LONG", "K crossed above D"

    # Bearish crossover
    if (
        ind.prev_stoch_k is not None
        and ind.prev_stoch_d is not None
        and ind.prev_stoch_k >= ind.prev_stoch_d
        and ind.stoch_k < ind.stoch_d
    ):
        return "SHORT", "K crossed below D"

    if ind.stoch_k < 30:
        return "LONG", f"oversold zone ({ind.stoch_k:.1f})"
    elif ind.stoch_k > 70:
        return "SHORT", f"overbought zone ({ind.stoch_k:.1f})"

    return "NEUTRAL", f"neutral ({ind.stoch_k:.1f})"


def _vote_volume(ind: IndicatorSet) -> tuple[str, str]:
    """Volume confirmation - above average volume confirms trend."""
    if ind.volume_sma is None or ind.volume_sma == 0:
        return "NEUTRAL", "no data"

    ratio = ind.volume / ind.volume_sma

    if ratio > 1.5:
        # High volume - confirms current price direction
        if ind.ema_fast is not None and ind.close > ind.ema_fast:
            return "LONG", f"high volume bullish ({ratio:.1f}x avg)"
        elif ind.ema_fast is not None and ind.close < ind.ema_fast:
            return "SHORT", f"high volume bearish ({ratio:.1f}x avg)"
    elif ratio < 0.5:
        return "NEUTRAL", f"low volume ({ratio:.1f}x avg)"

    return "NEUTRAL", f"normal volume ({ratio:.1f}x avg)"


def _vote_adx(ind: IndicatorSet) -> tuple[str, str]:
    """ADX trend strength filter."""
    if ind.adx is None:
        return "NEUTRAL", "no data"

    if ind.adx >= 25:
        # Strong trend - agree with EMA trend
        if ind.ema_fast is not None and ind.ema_slow is not None:
            if ind.ema_fast > ind.ema_slow:
                return "LONG", f"strong uptrend (ADX={ind.adx:.1f})"
            else:
                return "SHORT", f"strong downtrend (ADX={ind.adx:.1f})"
        return "NEUTRAL", f"strong trend no EMA (ADX={ind.adx:.1f})"

    return "NEUTRAL", f"weak trend (ADX={ind.adx:.1f})"
