"""Performance statistics and overfitting tests (Bailey & Lopez de Prado).

- deflated_sharpe: probability the observed Sharpe beats the best Sharpe
  expected from N trials of pure noise (DSR, 2014).
- pbo_cscv: probability of backtest overfitting via combinatorially symmetric
  cross-validation (2015).
"""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist

import numpy as np

from src.trend.config import DAYS_PER_YEAR

EULER_GAMMA = 0.5772156649
_N = NormalDist()


def daily_returns(ts: np.ndarray, bar_returns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Compound bar returns into UTC-day simple returns."""
    day = ts // 86400
    keys, inv = np.unique(day, return_inverse=True)
    logs = np.zeros(len(keys))
    np.add.at(logs, inv, np.log1p(np.maximum(bar_returns, -0.999999)))
    return keys * 86400, np.expm1(logs)


def sharpe(r: np.ndarray, periods: int = DAYS_PER_YEAR) -> float:
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return float(r.mean() / sd * math.sqrt(periods)) if sd > 0 else 0.0


def max_drawdown(r: np.ndarray) -> float:
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1.0).min()) if len(eq) else 0.0


def cagr(r: np.ndarray, periods: int = DAYS_PER_YEAR) -> float:
    if len(r) == 0:
        return 0.0
    total = float(np.prod(1.0 + r))
    return total ** (periods / len(r)) - 1.0 if total > 0 else -1.0


def summary(r: np.ndarray) -> dict[str, float]:
    return {"sharpe": sharpe(r), "cagr": cagr(r), "max_dd": max_drawdown(r),
            "vol": float(r.std(ddof=1) * math.sqrt(DAYS_PER_YEAR)) if len(r) > 1 else 0.0,
            "days": float(len(r))}


def _moments(r: np.ndarray) -> tuple[float, float]:
    m, sd = r.mean(), r.std()
    if sd == 0:
        return 0.0, 3.0
    z = (r - m) / sd
    return float((z ** 3).mean()), float((z ** 4).mean())


def expected_max_sharpe(n_trials: int, sr_var: float) -> float:
    """Expected maximum per-period Sharpe among n_trials of zero-skill strategies."""
    if n_trials < 2 or sr_var <= 0:
        return 0.0
    a = _N.inv_cdf(1.0 - 1.0 / n_trials)
    b = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_var) * ((1 - EULER_GAMMA) * a + EULER_GAMMA * b)


def deflated_sharpe(r: np.ndarray, n_trials: int, sr_var: float) -> float:
    """DSR on per-period (daily) returns. sr_var = variance of per-period Sharpe across trials."""
    t = len(r)
    sd = r.std(ddof=1)
    if t < 3 or sd == 0:
        return 0.0
    sr = r.mean() / sd
    skew, kurt = _moments(r)
    sr0 = expected_max_sharpe(n_trials, sr_var)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return 0.0
    return _N.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom))


def pbo_cscv(m: np.ndarray, n_blocks: int = 16) -> float:
    """m: (T, K) per-period returns of K variants. Returns PBO in [0, 1]."""
    t_len, k = m.shape
    if k < 2:
        return float("nan")
    edges = np.linspace(0, t_len, n_blocks + 1).astype(int)
    s1 = np.array([m[a:b].sum(0) for a, b in itertools.pairwise(edges)])
    s2 = np.array([(m[a:b] ** 2).sum(0) for a, b in itertools.pairwise(edges)])
    cnt = np.diff(edges).astype(float)[:, None]
    tot1, tot2, totn = s1.sum(0), s2.sum(0), cnt.sum()

    def _sr(a1: np.ndarray, a2: np.ndarray, n: float) -> np.ndarray:
        mu = a1 / n
        var = np.maximum(a2 / n - mu * mu, 1e-18)
        return mu / np.sqrt(var)

    below = 0
    combos = list(itertools.combinations(range(n_blocks), n_blocks // 2))
    for c in combos:
        idx = list(c)
        i1, i2, ni = s1[idx].sum(0), s2[idx].sum(0), cnt[idx].sum()
        sr_is = _sr(i1, i2, ni)
        sr_oos = _sr(tot1 - i1, tot2 - i2, totn - ni)
        best = int(np.argmax(sr_is))
        rank = (sr_oos < sr_oos[best]).sum() + 1          # 1..K
        omega = rank / (k + 1)
        if math.log(omega / (1 - omega)) <= 0:
            below += 1
    return below / len(combos)
