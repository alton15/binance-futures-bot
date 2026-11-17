"""Point-in-time universe, inverse-vol sizing and the bar-by-bar portfolio simulator.

`target_weights` is the single sizing function used by both the backtest and
the paper engine (parity).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.trend.config import (
    BARS_PER_DAY,
    BASELINE_FUNDING_PER_BAR,
    DAYS_PER_YEAR,
    CostConfig,
    PortfolioConfig,
)

ANNUAL_BARS = BARS_PER_DAY * DAYS_PER_YEAR
VOL_FLOOR = 0.10                     # annualized; avoids huge weights on dead-flat series


def realized_vol(close: np.ndarray, window_bars: int) -> np.ndarray:
    """Annualized rolling std of 4h log returns, using data <= t."""
    with np.errstate(invalid="ignore", divide="ignore"):
        lr = np.log(close[1:] / close[:-1])
    lr = np.vstack([np.full((1, close.shape[1]), np.nan, dtype=lr.dtype), lr])
    sd = pd.DataFrame(lr).rolling(window_bars, min_periods=window_bars // 2).std().to_numpy()
    return np.maximum(sd * np.sqrt(ANNUAL_BARS), VOL_FLOOR).astype(np.float32)


def universe_mask(close: np.ndarray, qvol: np.ndarray, cfg: PortfolioConfig) -> np.ndarray:
    """Top-N by trailing quote volume among contracts with enough history (bool (T, N))."""
    vol_bars = cfg.volume_window_d * BARS_PER_DAY
    hist_bars = cfg.min_history_d * BARS_PER_DAY
    seen = pd.DataFrame(np.isfinite(close)).cumsum().to_numpy()
    liq = pd.DataFrame(qvol).rolling(vol_bars, min_periods=vol_bars).sum().to_numpy(copy=True)
    liq[(seen < hist_bars) | ~np.isfinite(close)] = np.nan
    rank = pd.DataFrame(liq).rank(axis=1, ascending=False, method="first").to_numpy()
    return np.nan_to_num(rank, nan=np.inf) <= cfg.universe_size


def target_weights(signal: np.ndarray, vol: np.ndarray, in_universe: np.ndarray,
                   cfg: PortfolioConfig) -> np.ndarray:
    """One row (or many rows) of target weights as a fraction of equity."""
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = signal * (cfg.target_vol / vol) / cfg.universe_size
    raw = np.where(in_universe & np.isfinite(raw), raw, 0.0)
    raw = np.clip(raw, -cfg.max_weight, cfg.max_weight)
    gross = np.abs(raw).sum(axis=-1, keepdims=True)
    scale = np.where(gross > cfg.max_gross, cfg.max_gross / np.maximum(gross, 1e-12), 1.0)
    return (raw * scale).astype(np.float64)


def apply_band(current: np.ndarray, target: np.ndarray, band: float) -> np.ndarray:
    """Keep current weight unless the change is material or the direction flips."""
    flip = np.sign(current) != np.sign(target)
    big = np.abs(target - current) > band * np.abs(target)
    return np.where(flip | big, target, current)


@dataclass(frozen=True)
class SimResult:
    ts: np.ndarray            # (T,) bar open of the bar whose return is recorded
    returns: np.ndarray       # (T,) net portfolio return per bar
    gross: np.ndarray         # (T,) gross exposure held during the bar
    turnover: np.ndarray      # (T,) traded notional / equity at the bar open
    fees: np.ndarray          # (T,) cost fraction
    funding: np.ndarray       # (T,) funding fraction (positive = paid)


def simulate(close: np.ndarray, funding: np.ndarray, targets: np.ndarray, ts: np.ndarray,
             cfg: PortfolioConfig, costs: CostConfig) -> SimResult:
    """Decide at close t, hold over bar t+1. Contracts about to delist are flattened at t."""
    t_len, n = close.shape
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = np.nan_to_num(close[1:] / close[:-1] - 1.0, nan=0.0, posinf=0.0, neginf=0.0)
    fund = np.where(np.isfinite(funding), funding, BASELINE_FUNDING_PER_BAR)
    alive = np.isfinite(close)
    tradable = alive[:-1] & alive[1:]
    w = np.zeros(n)
    out = np.zeros((5, t_len - 1))
    per_side = costs.per_side
    for t in range(t_len - 1):
        new = w
        if t % cfg.rebalance_every == 0:
            new = apply_band(w, targets[t], cfg.rebalance_band)
        new = np.where(tradable[t], new, 0.0)
        traded = np.abs(new - w).sum()
        fee = per_side * traded
        w = new
        r = ret[t]
        fpay = float(np.dot(w, fund[t + 1] * alive[t + 1]))
        pnl = float(np.dot(w, r)) - fpay - fee
        out[:, t] = (pnl, np.abs(w).sum(), traded, fee, fpay)
        growth = 1.0 + pnl
        w = w * (1.0 + r) / growth if growth > 0 else np.zeros(n)
    return SimResult(ts[1:], *out)
