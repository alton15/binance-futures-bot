"""Incremental per-symbol features on 1s bars.

Must match backtest/features.py (vectorized) exactly; see tests/scalper/test_parity.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src.scalper.types import Bar


@dataclass(frozen=True)
class FeatureSnapshot:
    ts: int
    close: float
    move_bps: float    # log return over lookback, in bps
    z: float           # move / (sigma_1s * sqrt(lookback))
    burst: float       # notional over lookback / average notional per lookback


class SymbolFeatures:
    """Ring-buffer feature calculator. One instance per symbol."""

    def __init__(self, lookback_s: int, sigma_window_s: int) -> None:
        if lookback_s <= 0 or sigma_window_s <= lookback_s:
            raise ValueError("need 0 < lookback_s < sigma_window_s")
        self._lookback = lookback_s
        self._window = sigma_window_s
        self._min_periods = sigma_window_s // 2
        size = sigma_window_s + 1
        self._log_close = np.full(size, np.nan)
        self._notional = np.zeros(size)
        self._count = 0
        self._last_ts: int | None = None

    @property
    def last_ts(self) -> int | None:
        return self._last_ts

    def push(self, bar: Bar) -> FeatureSnapshot | None:
        """Add a bar; returns a snapshot once enough history exists."""
        if self._last_ts is not None and bar.ts <= self._last_ts:
            return None
        size = self._log_close.shape[0]
        idx = self._count % size
        self._log_close[idx] = math.log(bar.close)
        self._notional[idx] = bar.buy_notional + bar.sell_notional
        self._count += 1
        self._last_ts = bar.ts
        return self._snapshot(bar)

    def _ordered(self, arr: np.ndarray, n: int) -> np.ndarray:
        """Last n values in chronological order."""
        size = arr.shape[0]
        end = self._count % size
        if self._count < size:
            return arr[max(0, self._count - n):self._count]
        rolled = np.concatenate((arr[end:], arr[:end]))
        return rolled[-n:]

    def _snapshot(self, bar: Bar) -> FeatureSnapshot | None:
        if self._count <= self._lookback:
            return None
        closes = self._ordered(self._log_close, self._window + 1)
        rets = np.diff(closes)
        if rets.shape[0] < self._min_periods:
            return None
        sigma = float(np.std(rets, ddof=1))
        move = float(closes[-1] - closes[-1 - self._lookback])
        z = move / (sigma * math.sqrt(self._lookback)) if sigma > 0 else 0.0
        notional = self._ordered(self._notional, self._window)
        recent = float(notional[-self._lookback:].sum())
        base = float(notional.sum()) * (self._lookback / self._window)
        burst = recent / base if base > 0 else 0.0
        return FeatureSnapshot(bar.ts, bar.close, move * 1e4, z, burst)
