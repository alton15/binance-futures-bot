"""Vectorized signal detection + numba exit engine on 1s bars.

Execution assumptions are deliberately conservative:
- taker entry fills one bar after the signal, at close +/- half spread
- maker entry (limit at signal price) fills only if price trades *through* it
- TP is a resting limit: fills only when price trades through
- SL fills at the worse of stop/close plus stop slippage; SL checked before TP
"""

from __future__ import annotations

from dataclasses import dataclass

import numba as nb
import numpy as np
import pandas as pd

from src.scalper.config import MAKER_FEE, TAKER_FEE, StrategyConfig
from src.scalper.risk.sizing import STOP_SLIPPAGE


@dataclass(frozen=True)
class ExecAssumptions:
    latency_s: int = 1
    half_spread_bps: float = 1.0
    stop_slip_bps: float = STOP_SLIPPAGE * 1e4  # same constant the paper broker uses
    maker_fee_bps: float = MAKER_FEE * 1e4
    taker_fee_bps: float = TAKER_FEE * 1e4


@dataclass(frozen=True)
class Features:
    sec: np.ndarray
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    z: np.ndarray
    move_bps: np.ndarray
    burst: np.ndarray


def build_features(bars: pd.DataFrame, lookback_s: int, sigma_window_s: int) -> Features:
    """Vectorized twin of strategy.features.SymbolFeatures."""
    log_c = np.log(bars["close"])
    lr = log_c.diff()
    min_p = sigma_window_s // 2
    sigma = lr.rolling(sigma_window_s, min_periods=min_p).std().to_numpy()
    r = (log_c - log_c.shift(lookback_s)).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sigma > 0, r / (sigma * np.sqrt(lookback_s)), 0.0)
    vol = bars["buy"] + bars["sell"]
    base = vol.rolling(sigma_window_s, min_periods=1).sum() * (lookback_s / sigma_window_s)
    recent = vol.rolling(lookback_s, min_periods=1).sum()
    burst = (recent / base.replace(0, np.nan)).fillna(0.0).to_numpy()
    z = np.where(np.isfinite(sigma), z, np.nan)
    return Features(bars.index.to_numpy(), bars["close"].to_numpy(), bars["high"].to_numpy(),
                    bars["low"].to_numpy(), z, r * 1e4, burst)


@nb.njit(cache=True)
def _run(close, high, low, cand, dirs, moves, tp_frac, sl_frac, max_hold, maker,
         maker_wait, latency, cooldown, half_spread, stop_slip, maker_fee, taker_fee):
    n = close.shape[0]
    m = cand.shape[0]
    out_i = np.empty(m, np.int64)
    out_net = np.empty(m)
    out_r = np.empty(m)
    out_hold = np.empty(m, np.int64)
    out_kind = np.empty(m, np.int64)   # 0 tp, 1 sl, 2 time
    out_dir = np.empty(m, np.int64)
    k = 0
    busy_until = -1
    for i in range(m):
        t = cand[i]
        if t <= busy_until:
            continue
        d = dirs[i]
        move = abs(moves[i]) * 1e-4
        if not maker:
            te = t + latency
            if te >= n:
                continue
            entry = close[te] * (1.0 + d * half_spread * 1e-4)
            fee_in = taker_fee
        else:
            entry = close[t]
            te = -1
            for s in range(t + latency, min(t + latency + maker_wait, n)):
                if (d > 0 and low[s] < entry) or (d < 0 and high[s] > entry):
                    te = s
                    break
            if te < 0:
                busy_until = t + latency + maker_wait
                continue
            fee_in = maker_fee
        tp = entry * (1.0 + d * tp_frac * move)
        sl = entry * (1.0 - d * sl_frac * move)
        kind = 2
        fee_out = taker_fee
        exit_px = 0.0
        end = min(te + max_hold, n - 1)
        s = te + 1
        while s <= end:
            if (d > 0 and low[s] <= sl) or (d < 0 and high[s] >= sl):
                px = min(sl, close[s]) if d > 0 else max(sl, close[s])
                exit_px = px * (1.0 - d * stop_slip * 1e-4)
                kind = 1
                break
            if (d > 0 and high[s] > tp) or (d < 0 and low[s] < tp):
                exit_px = tp
                kind = 0
                fee_out = maker_fee
                break
            s += 1
        if kind == 2:
            s = end
            exit_px = close[s] * (1.0 - d * half_spread * 1e-4)
        net = d * (exit_px / entry - 1.0) * 1e4 - fee_in - fee_out
        risk_bps = sl_frac * move * 1e4 + fee_in + taker_fee + stop_slip
        out_i[k] = te
        out_net[k] = net
        out_r[k] = net / risk_bps
        out_hold[k] = s - te
        out_kind[k] = kind
        out_dir[k] = d
        k += 1
        busy_until = s + cooldown
    return out_i[:k], out_net[:k], out_r[:k], out_hold[:k], out_kind[:k], out_dir[:k]


def candidate_mask(f: Features, cfg: StrategyConfig) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        ok = (np.abs(f.z) > cfg.z_entry) & (np.abs(f.move_bps) > cfg.min_move_bps)
        if cfg.burst_min > 0:
            ok &= f.burst > cfg.burst_min
    return ok & np.isfinite(f.z)


TRADE_COLUMNS = ["ts", "net_bps", "r", "hold", "kind", "dir"]


def simulate(f: Features, cfg: StrategyConfig, ex: ExecAssumptions = ExecAssumptions()) -> pd.DataFrame:
    """Simulate the fade strategy on one symbol; one row per trade."""
    idx = np.nonzero(candidate_mask(f, cfg))[0].astype(np.int64)
    if idx.size == 0:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    sign = 1 if cfg.follow else -1
    dirs = (sign * np.sign(f.move_bps[idx])).astype(np.int64)
    i, net, r, hold, kind, d = _run(
        f.close, f.high, f.low, idx, dirs, f.move_bps[idx], cfg.tp_frac, cfg.sl_frac,
        cfg.max_hold_s, cfg.maker_entry, cfg.maker_wait_s, ex.latency_s, cfg.cooldown_s,
        ex.half_spread_bps, ex.stop_slip_bps, ex.maker_fee_bps, ex.taker_fee_bps,
    )
    return pd.DataFrame({"ts": f.sec[i], "net_bps": net, "r": r, "hold": hold,
                         "kind": kind, "dir": d})
