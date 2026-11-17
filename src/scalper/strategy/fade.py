"""Volatility-breakout signal: statistically extreme moves, traded with or against.

Backtest status (60d, 10 alts, VIP0 fees, realistic fills): neither fading
nor following such moves showed a robust edge across time folds. The module
is kept parameterized so forward paper tests can compare both modes.
"""

from __future__ import annotations

import math

from src.scalper.config import StrategyConfig
from src.scalper.strategy.features import FeatureSnapshot
from src.scalper.types import Side, Signal


def bracket(entry: float, side: Side, move_bps: float, cfg: StrategyConfig) -> tuple[float, float]:
    """TP/SL prices for a fill at `entry`, distances proportional to |move|."""
    move = abs(move_bps) * 1e-4
    d = int(side)
    tp = entry * (1.0 + d * cfg.tp_frac * move)
    sl = entry * (1.0 - d * cfg.sl_frac * move)
    return tp, sl


def evaluate(symbol: str, snap: FeatureSnapshot, cfg: StrategyConfig) -> Signal | None:
    """Return a signal if the snapshot is an extreme move, else None."""
    if not math.isfinite(snap.z):
        return None
    if abs(snap.z) <= cfg.z_entry or abs(snap.move_bps) <= cfg.min_move_bps:
        return None
    if cfg.burst_min > 0 and snap.burst <= cfg.burst_min:
        return None
    up = snap.move_bps > 0
    side = (Side.LONG if up else Side.SHORT) if cfg.follow else (Side.SHORT if up else Side.LONG)
    tp, sl = bracket(snap.close, side, snap.move_bps, cfg)
    return Signal(
        symbol=symbol, side=side, ts=snap.ts, price=snap.close,
        move_bps=snap.move_bps, z=snap.z, tp_price=tp, sl_price=sl,
    )
