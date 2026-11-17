"""Trend signals on (T, N) price arrays. Every value at row t uses data <= t only.

Each kind produces a per-lookback vote in {-1, 0, +1}; the signal is the mean
vote over the configured lookbacks (an ensemble, which avoids betting on one
parameter).
"""

from __future__ import annotations

import numba as nb
import numpy as np
import pandas as pd

from src.trend.config import BARS_PER_DAY, SignalConfig

KINDS = ("donchian", "tsmom", "ema")
EMA_FAST_FRAC = 0.25                 # fast EMA span = lookback * this


@nb.njit(cache=True)
def _channel_state(close: np.ndarray, upper: np.ndarray, lower: np.ndarray,
                   exit_hi: np.ndarray, exit_lo: np.ndarray) -> np.ndarray:
    """Breakout state machine: enter on an L-bar channel break, exit on the L/2 opposite channel."""
    t_len, n = close.shape
    out = np.zeros((t_len, n), dtype=np.float32)
    for j in range(n):
        state = 0
        for t in range(t_len):
            c = close[t, j]
            if np.isnan(c) or np.isnan(upper[t, j]):
                state = 0
                continue
            if state == 1 and c <= exit_lo[t, j]:
                state = 0
            elif state == -1 and c >= exit_hi[t, j]:
                state = 0
            if state <= 0 and c >= upper[t, j]:
                state = 1
            elif state >= 0 and c <= lower[t, j]:
                state = -1
            out[t, j] = state
    return out


def _roll(a: np.ndarray, window: int, how: str) -> np.ndarray:
    """Rolling max/min over the PREVIOUS `window` bars (excludes bar t)."""
    df = pd.DataFrame(a).shift(1).rolling(window, min_periods=window)
    return (df.max() if how == "max" else df.min()).to_numpy(dtype=np.float32)


def donchian_vote(close: np.ndarray, high: np.ndarray, low: np.ndarray, lookback: int) -> np.ndarray:
    half = max(lookback // 2, 1)
    return _channel_state(close, _roll(high, lookback, "max"), _roll(low, lookback, "min"),
                          _roll(high, half, "max"), _roll(low, half, "min"))


def tsmom_vote(close: np.ndarray, lookback: int) -> np.ndarray:
    past = pd.DataFrame(close).shift(lookback).to_numpy()
    with np.errstate(invalid="ignore"):
        v = np.sign(close / past - 1.0)
    return np.nan_to_num(v, nan=0.0).astype(np.float32)


def ema_vote(close: np.ndarray, lookback: int) -> np.ndarray:
    df = pd.DataFrame(close)
    fast = df.ewm(span=max(int(lookback * EMA_FAST_FRAC), 2), adjust=False, ignore_na=True).mean()
    slow = df.ewm(span=lookback, adjust=False, ignore_na=True).mean()
    seen = df.notna().cumsum().to_numpy()
    v = np.sign((fast - slow).to_numpy())
    v[(seen < lookback) | np.isnan(close)] = 0.0
    return np.nan_to_num(v, nan=0.0).astype(np.float32)


def vote(kind: str, close: np.ndarray, high: np.ndarray, low: np.ndarray, lookback: int) -> np.ndarray:
    if kind == "donchian":
        return donchian_vote(close, high, low, lookback)
    if kind == "tsmom":
        return tsmom_vote(close, lookback)
    if kind == "ema":
        return ema_vote(close, lookback)
    raise ValueError(f"unknown signal kind {kind!r}; expected one of {KINDS}")


def apply_regime(sig: np.ndarray, regime: np.ndarray) -> np.ndarray:
    """Zero longs when the market regime is not up and shorts when it is not down."""
    r = regime[:, None]
    return np.where(sig > 0, sig * (r > 0), sig * (r < 0)).astype(np.float32)


def compute_signal(cfg: SignalConfig, close: np.ndarray, high: np.ndarray, low: np.ndarray,
                   cache: dict | None = None, regime_col: int | None = None) -> np.ndarray:
    """Mean vote over lookbacks; optional cache keyed by (kind, lookback_bars).

    regime_col: column of the market proxy (BTC); required when cfg.regime_filter.
    """
    votes = []
    for d in cfg.lookbacks_d:
        lb = d * BARS_PER_DAY
        key = (cfg.kind, lb)
        if cache is not None and key in cache:
            v = cache[key]
        else:
            v = vote(cfg.kind, close, high, low, lb)
            if cache is not None:
                cache[key] = v
        votes.append(v)
    sig = np.mean(votes, axis=0)
    if cfg.regime_filter:
        if regime_col is None:
            raise ValueError("regime_filter requires regime_col (BTC column)")
        sig = apply_regime(sig, sig[:, regime_col].copy())
    if cfg.long_only:
        sig = np.clip(sig, 0.0, None)
    return sig.astype(np.float32)
