"""Technical indicator calculator using pandas-ta."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
import pandas_ta as ta

from config.settings import INDICATORS

logger = logging.getLogger(__name__)


@dataclass
class IndicatorSet:
    """Computed technical indicators for a symbol."""

    symbol: str
    timeframe: str
    close: float
    volume: float

    # RSI
    rsi: float | None = None

    # MACD
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None

    # Bollinger Bands
    bb_upper: float | None = None
    bb_mid: float | None = None
    bb_lower: float | None = None

    # EMAs
    ema_fast: float | None = None   # 9
    ema_mid: float | None = None    # 21
    ema_slow: float | None = None   # 200

    # ATR
    atr: float | None = None

    # ADX
    adx: float | None = None

    # Stochastic
    stoch_k: float | None = None
    stoch_d: float | None = None

    # Previous values for crossover detection
    prev_ema_fast: float | None = None
    prev_ema_mid: float | None = None
    prev_macd: float | None = None
    prev_macd_signal: float | None = None
    prev_macd_hist: float | None = None
    prev_stoch_k: float | None = None
    prev_stoch_d: float | None = None

    # Volume
    volume_sma: float | None = None  # 20-period volume SMA


def compute_indicators(
    ohlcv: list[list],
    symbol: str,
    timeframe: str = "1h",
) -> IndicatorSet | None:
    """Compute all technical indicators from OHLCV data.

    Args:
        ohlcv: List of [timestamp, open, high, low, close, volume]
        symbol: Symbol name
        timeframe: Candle timeframe

    Returns:
        IndicatorSet or None if insufficient data
    """
    if len(ohlcv) < INDICATORS["ema_slow"] + 10:
        logger.warning(
            "%s: insufficient data (%d candles, need %d)",
            symbol, len(ohlcv), INDICATORS["ema_slow"] + 10,
        )
        return None

    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

    # RSI
    rsi_series = ta.rsi(df["close"], length=INDICATORS["rsi_period"])

    # MACD
    macd_df = ta.macd(
        df["close"],
        fast=INDICATORS["macd_fast"],
        slow=INDICATORS["macd_slow"],
        signal=INDICATORS["macd_signal"],
    )

    # Bollinger Bands
    bb_df = ta.bbands(df["close"], length=INDICATORS["bb_period"], std=INDICATORS["bb_std"])

    # EMAs
    ema_fast = ta.ema(df["close"], length=INDICATORS["ema_fast"])
    ema_mid = ta.ema(df["close"], length=INDICATORS["ema_mid"])
    ema_slow = ta.ema(df["close"], length=INDICATORS["ema_slow"])

    # ATR
    atr_series = ta.atr(df["high"], df["low"], df["close"], length=INDICATORS["atr_period"])

    # ADX
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=INDICATORS["adx_period"])

    # Stochastic
    stoch_df = ta.stoch(
        df["high"], df["low"], df["close"],
        k=INDICATORS["stoch_k"], d=INDICATORS["stoch_d"],
    )

    # Volume SMA
    vol_sma = ta.sma(df["volume"], length=20)

    # Extract latest values safely
    def last(series: pd.Series | None) -> float | None:
        if series is None or series.empty:
            return None
        val = series.iloc[-1]
        return float(val) if pd.notna(val) else None

    def prev(series: pd.Series | None) -> float | None:
        if series is None or len(series) < 2:
            return None
        val = series.iloc[-2]
        return float(val) if pd.notna(val) else None

    # MACD column names
    macd_col = f"MACD_{INDICATORS['macd_fast']}_{INDICATORS['macd_slow']}_{INDICATORS['macd_signal']}"
    macd_sig_col = f"MACDs_{INDICATORS['macd_fast']}_{INDICATORS['macd_slow']}_{INDICATORS['macd_signal']}"
    macd_hist_col = f"MACDh_{INDICATORS['macd_fast']}_{INDICATORS['macd_slow']}_{INDICATORS['macd_signal']}"

    # BB column names - pandas-ta uses format BBU_{period}_{std}_{std}
    bb_std = INDICATORS['bb_std']
    bb_upper_col = f"BBU_{INDICATORS['bb_period']}_{bb_std}_{bb_std}"
    bb_mid_col = f"BBM_{INDICATORS['bb_period']}_{bb_std}_{bb_std}"
    bb_lower_col = f"BBL_{INDICATORS['bb_period']}_{bb_std}_{bb_std}"

    # ADX column name
    adx_col = f"ADX_{INDICATORS['adx_period']}"

    # Stochastic column names
    stoch_k_col = f"STOCHk_{INDICATORS['stoch_k']}_{INDICATORS['stoch_d']}_3"
    stoch_d_col = f"STOCHd_{INDICATORS['stoch_k']}_{INDICATORS['stoch_d']}_3"

    return IndicatorSet(
        symbol=symbol,
        timeframe=timeframe,
        close=float(df["close"].iloc[-1]),
        volume=float(df["volume"].iloc[-1]),
        rsi=last(rsi_series),
        macd=last(macd_df[macd_col]) if macd_df is not None else None,
        macd_signal=last(macd_df[macd_sig_col]) if macd_df is not None else None,
        macd_hist=last(macd_df[macd_hist_col]) if macd_df is not None else None,
        bb_upper=last(bb_df[bb_upper_col]) if bb_df is not None else None,
        bb_mid=last(bb_df[bb_mid_col]) if bb_df is not None else None,
        bb_lower=last(bb_df[bb_lower_col]) if bb_df is not None else None,
        ema_fast=last(ema_fast),
        ema_mid=last(ema_mid),
        ema_slow=last(ema_slow),
        atr=last(atr_series),
        adx=last(adx_df[adx_col]) if adx_df is not None else None,
        stoch_k=last(stoch_df[stoch_k_col]) if stoch_df is not None else None,
        stoch_d=last(stoch_df[stoch_d_col]) if stoch_df is not None else None,
        prev_ema_fast=prev(ema_fast),
        prev_ema_mid=prev(ema_mid),
        prev_macd=prev(macd_df[macd_col]) if macd_df is not None else None,
        prev_macd_signal=prev(macd_df[macd_sig_col]) if macd_df is not None else None,
        prev_macd_hist=prev(macd_df[macd_hist_col]) if macd_df is not None else None,
        prev_stoch_k=prev(stoch_df[stoch_k_col]) if stoch_df is not None else None,
        prev_stoch_d=prev(stoch_df[stoch_d_col]) if stoch_df is not None else None,
        volume_sma=last(vol_sma),
    )
