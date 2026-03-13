"""Strategy analyzer - per-coin comprehensive analysis."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from config.settings import MARKET, SIGNALS
from src.clients.binance_rest import BinanceClient
from src.indicators.calculator import compute_indicators
from src.indicators.signals import generate_signal, Signal
from src.db.models import save_signal, save_indicator_snapshot

if TYPE_CHECKING:
    from config.profiles import ProfileConfig

logger = logging.getLogger(__name__)


async def analyze_coin(
    client: BinanceClient,
    symbol: str,
    timeframe: str | None = None,
    profile: ProfileConfig | None = None,
    confirm_timeframes: list[str] | None = None,
) -> dict[str, Any] | None:
    """Analyze a coin using technical indicators and multi-timeframe confirmation.

    Steps:
    1. Fetch OHLCV for primary timeframe (1h)
    2. Compute indicators
    3. Generate signal
    4. Confirm with secondary timeframes (15m, 4h)
    5. Save signal and indicator snapshot

    Returns:
        Analysis result dict or None if insufficient data.
    """
    if timeframe is None:
        timeframe = MARKET["default_timeframe"]

    # Primary timeframe analysis
    try:
        ohlcv = await client.fetch_ohlcv(symbol, timeframe, limit=250)
    except Exception as e:
        logger.warning("Failed to fetch OHLCV for %s: %s", symbol, e)
        return None

    indicators = compute_indicators(ohlcv, symbol, timeframe)
    if indicators is None:
        return None

    primary_signal = generate_signal(indicators)

    # Multi-timeframe confirmation
    mtf_confirms = 0
    mtf_list = confirm_timeframes or MARKET.get("confirm_timeframes", [])
    for tf in mtf_list:
        try:
            tf_ohlcv = await client.fetch_ohlcv(symbol, tf, limit=250)
            tf_indicators = compute_indicators(tf_ohlcv, symbol, tf)
            if tf_indicators is None:
                continue
            tf_signal = generate_signal(tf_indicators)
            if tf_signal.direction == primary_signal.direction:
                mtf_confirms += 1
        except Exception as e:
            logger.debug("MTF check failed for %s %s: %s", symbol, tf, e)

    # Adjust strength based on MTF confirmation
    adjusted_strength = primary_signal.strength
    if mtf_confirms >= 2:
        adjusted_strength = min(1.0, primary_signal.strength * 1.15)
    elif mtf_confirms == 0 and primary_signal.direction != "NEUTRAL":
        adjusted_strength *= 0.5

    # Signal quality filters (profile-specific penalties)
    if primary_signal.direction != "NEUTRAL" and profile:
        adjusted_strength = _apply_quality_filters(
            adjusted_strength, primary_signal, profile,
        )

    # Save signal to DB
    import json
    details_json = json.dumps(primary_signal.details, default=str)
    signal_id = await save_signal(
        symbol=symbol,
        direction=primary_signal.direction,
        strength=adjusted_strength,
        confirming_count=primary_signal.confirming_count,
        timeframe=timeframe,
        indicator_details=details_json,
    )

    # Save indicator snapshot
    await save_indicator_snapshot(
        symbol=symbol,
        timeframe=timeframe,
        close_price=indicators.close,
        volume=indicators.volume,
        rsi=indicators.rsi,
        macd=indicators.macd,
        macd_signal=indicators.macd_signal,
        macd_hist=indicators.macd_hist,
        bb_upper=indicators.bb_upper,
        bb_mid=indicators.bb_mid,
        bb_lower=indicators.bb_lower,
        ema_fast=indicators.ema_fast,
        ema_mid=indicators.ema_mid,
        ema_slow=indicators.ema_slow,
        atr=indicators.atr,
        adx=indicators.adx,
        stoch_k=indicators.stoch_k,
        stoch_d=indicators.stoch_d,
    )

    # Use profile-specific signal thresholds if available
    min_confirming = profile.get_signal("min_confirming") if profile else SIGNALS["min_confirming"]
    min_strength = profile.get_signal("min_strength") if profile else SIGNALS["min_strength"]

    result = {
        "symbol": symbol,
        "signal_id": signal_id,
        "direction": primary_signal.direction,
        "strength": adjusted_strength,
        "confirming_count": primary_signal.confirming_count,
        "mtf_confirms": mtf_confirms,
        "is_actionable": (
            primary_signal.direction != "NEUTRAL"
            and primary_signal.confirming_count >= min_confirming
            and adjusted_strength >= min_strength
            and mtf_confirms >= 1
        ),
        "close_price": indicators.close,
        "atr": indicators.atr,
        "rsi": indicators.rsi,
        "adx": indicators.adx,
        "details": primary_signal.details,
    }

    logger.info(
        "Analysis %s: %s strength=%.2f confirming=%d mtf=%d actionable=%s",
        symbol, result["direction"], result["strength"],
        result["confirming_count"], mtf_confirms, result["is_actionable"],
    )

    return result


def _apply_quality_filters(
    strength: float,
    signal: Signal,
    profile: ProfileConfig,
) -> float:
    """Apply profile-specific signal quality penalties.

    Filters:
    1. MACD opposition: heavy-weight indicator opposing signal direction
    2. Low volume: insufficient momentum for reliable signal
    3. BB conflict: Bollinger Band direction opposing signal direction

    Penalty of 1.0 = reject (strength forced to 0).
    """
    details = signal.details

    # 1. MACD opposition penalty (MACD has weight 2.0 - strongest indicator)
    macd_vote = details.get("macd", {})
    macd_dir = macd_vote.get("direction", "NEUTRAL")
    macd_penalty = profile.get_signal("macd_opposition_penalty")
    if macd_dir != "NEUTRAL" and macd_dir != signal.direction and macd_penalty > 0:
        if macd_penalty >= 1.0:
            logger.info(
                "Signal %s REJECTED: MACD opposes %s (macd=%s, penalty=reject)",
                signal.symbol, signal.direction, macd_dir,
            )
            return 0.0
        strength *= (1 - macd_penalty)
        logger.info(
            "Signal %s penalized: MACD opposes %s (-%s%%), new strength=%.2f",
            signal.symbol, signal.direction, int(macd_penalty * 100), strength,
        )

    # 2. Low volume penalty
    vol_vote = details.get("volume", {})
    vol_reason = vol_vote.get("reason", "")
    low_vol_threshold = profile.get_signal("low_volume_threshold")
    low_vol_penalty = profile.get_signal("low_volume_penalty")
    if low_vol_threshold > 0 and low_vol_penalty > 0:
        # Extract volume ratio from reason string (e.g., "low volume (0.3x avg)")
        vol_ratio = _extract_volume_ratio(vol_reason)
        if vol_ratio is not None and vol_ratio < low_vol_threshold:
            if low_vol_penalty >= 1.0:
                logger.info(
                    "Signal %s REJECTED: low volume %.1fx < %.1fx threshold",
                    signal.symbol, vol_ratio, low_vol_threshold,
                )
                return 0.0
            strength *= (1 - low_vol_penalty)
            logger.info(
                "Signal %s penalized: low volume %.1fx (-%s%%), new strength=%.2f",
                signal.symbol, vol_ratio, int(low_vol_penalty * 100), strength,
            )

    # 3. Bollinger Band conflict penalty
    bb_vote = details.get("bollinger", {})
    bb_dir = bb_vote.get("direction", "NEUTRAL")
    bb_penalty = profile.get_signal("bb_conflict_penalty")
    if bb_dir != "NEUTRAL" and bb_dir != signal.direction and bb_penalty > 0:
        strength *= (1 - bb_penalty)
        logger.info(
            "Signal %s penalized: BB opposes %s (-%s%%), new strength=%.2f",
            signal.symbol, signal.direction, int(bb_penalty * 100), strength,
        )

    return max(0.0, round(strength, 4))


def _extract_volume_ratio(reason: str) -> float | None:
    """Extract volume ratio from vote reason string.

    Examples: "low volume (0.3x avg)" → 0.3, "high volume bullish (1.9x avg)" → 1.9
    """
    import re
    match = re.search(r"\((\d+\.?\d*)x\s*avg\)", reason)
    if match:
        return float(match.group(1))
    return None
