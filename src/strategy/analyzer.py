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
