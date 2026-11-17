"""Grid backtest of trend variants with overfitting controls and an untouched holdout.

Every variant tried is logged (the trial count feeds the Deflated Sharpe).
Selection uses in-sample data only; the holdout year is reported, never used.

Usage:
    python -m src.trend.backtest.run --out data/trend/results
"""

from __future__ import annotations

import argparse
import itertools
import logging
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

from src.trend.backtest.stats import daily_returns, deflated_sharpe, pbo_cscv, summary
from src.trend.config import (
    BARS_PER_DAY,
    PANEL_PATH,
    REGIME_SYMBOL,
    TREND_DIR,
    CostConfig,
    PortfolioConfig,
    SignalConfig,
)
from src.trend.data.panel import Panel, load_panel
from src.trend.portfolio import realized_vol, simulate, target_weights, universe_mask
from src.trend.signals import compute_signal

logger = logging.getLogger(__name__)

HOLDOUT_START = int(pd.Timestamp("2025-11-01", tz="UTC").timestamp())
HOLDOUT_END = int(pd.Timestamp("2026-11-01", tz="UTC").timestamp())
EVAL_START = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp())   # 2020 = warmup / thin universe
MAX_UNIVERSE = 30

# Every grid ever evaluated stays listed: the total trial count deflates the Sharpe.
GRIDS: tuple[dict[str, list], ...] = (
    {   # round 1: signal family x horizon x universe x rebalance frequency
        "kind": ["donchian", "tsmom", "ema"],
        "lookbacks_d": [(10, 20, 40), (20, 40, 80), (40, 80, 160), (10, 20, 40, 80, 160)],
        "long_only": [False, True],
        "regime_filter": [False],
        "universe_size": [10, 20, 30],
        "rebalance_every": [1, BARS_PER_DAY],
    },
    {   # round 2: shorter horizon and a BTC regime filter (round 1 showed short horizons + regime dependence)
        "kind": ["donchian", "tsmom", "ema"],
        "lookbacks_d": [(5, 10, 20), (10, 20, 40)],
        "long_only": [False, True],
        "regime_filter": [True],
        "universe_size": [10, 20],
        "rebalance_every": [BARS_PER_DAY],
    },
    {
        "kind": ["donchian", "tsmom", "ema"],
        "lookbacks_d": [(5, 10, 20)],
        "long_only": [False, True],
        "regime_filter": [False],
        "universe_size": [10, 20],
        "rebalance_every": [BARS_PER_DAY],
    },
)


def variants(grids: tuple[dict[str, list], ...]) -> list[tuple[SignalConfig, PortfolioConfig]]:
    out = []
    for grid in grids:
        for vals in itertools.product(*grid.values()):
            g = dict(zip(grid, vals, strict=True))
            sig = SignalConfig(kind=g["kind"], lookbacks_d=g["lookbacks_d"], long_only=g["long_only"],
                               regime_filter=g["regime_filter"])
            port = replace(PortfolioConfig(), universe_size=g["universe_size"],
                           rebalance_every=g["rebalance_every"])
            out.append((sig, port))
    return out


def shrink_to_universe(p: Panel, max_size: int) -> Panel:
    """Keep only contracts that ever rank in the top `max_size` (speeds up the grid)."""
    mask = universe_mask(p.close, p.qvol, replace(PortfolioConfig(), universe_size=max_size))
    keep = np.flatnonzero(mask.any(axis=0))
    return Panel(p.ts, tuple(p.symbols[i] for i in keep), p.close[:, keep], p.high[:, keep],
                 p.low[:, keep], p.qvol[:, keep], p.funding[:, keep])


def run_variant(p: Panel, sig: SignalConfig, port: PortfolioConfig, costs: CostConfig,
                caches: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    signal = compute_signal(sig, p.close, p.high, p.low, caches.setdefault("votes", {}),
                            regime_col=p.symbols.index(REGIME_SYMBOL))
    vol_key = ("vol", port.vol_window_d)
    if vol_key not in caches:
        caches[vol_key] = realized_vol(p.close, port.vol_window_d * BARS_PER_DAY)
    uni_key = ("uni", port.universe_size)
    if uni_key not in caches:
        caches[uni_key] = universe_mask(p.close, p.qvol, port)
    targets = target_weights(signal, caches[vol_key], caches[uni_key], port)
    res = simulate(p.close, p.funding, targets, p.ts, port, costs)
    days, dr = daily_returns(res.ts, res.returns)
    years = (res.ts[-1] - res.ts[0]) / (365 * 86400)
    extra = {"turnover_yr": float(res.turnover.sum() / years), "fee_yr": float(res.fees.sum() / years),
             "funding_yr": float(res.funding.sum() / years), "avg_gross": float(res.gross.mean())}
    return days, dr, extra


def benchmarks(p: Panel, days: np.ndarray) -> dict[str, np.ndarray]:
    btc = p.symbols.index("BTCUSDT")
    close = pd.Series(p.close[:, btc], index=p.ts)
    daily_close = close.groupby(p.ts // 86400 * 86400).last().reindex(days).ffill()
    out = {"btc_hold": daily_close.pct_change().fillna(0.0).to_numpy()}
    uni = universe_mask(p.close, p.qvol, replace(PortfolioConfig(), universe_size=20))
    ew = target_weights(np.ones_like(p.close), np.full_like(p.close, 1.0), uni,
                        replace(PortfolioConfig(), universe_size=20, target_vol=1.0, max_gross=1.0))
    res = simulate(p.close, p.funding, ew, p.ts, replace(PortfolioConfig(), rebalance_every=BARS_PER_DAY),
                   CostConfig())
    d, r = daily_returns(res.ts, res.returns)
    out["ew_top20_long"] = pd.Series(r, index=d).reindex(days).fillna(0.0).to_numpy()
    return out


def _period(days: np.ndarray, start: int, end: int) -> np.ndarray:
    return (days >= start) & (days < end)


def _yearly_sharpes(days: np.ndarray, r: np.ndarray, end: int) -> list[float]:
    out = []
    for y in range(2021, 2026):
        lo = int(pd.Timestamp(f"{y}-01-01", tz="UTC").timestamp())
        hi = min(int(pd.Timestamp(f"{y + 1}-01-01", tz="UTC").timestamp()), end)
        m = _period(days, lo, hi)
        if m.sum() > 30:
            out.append(summary(r[m])["sharpe"])
    return out


def evaluate(p: Panel, out_dir: Path, costs: CostConfig) -> pd.DataFrame:
    vs = variants(GRIDS)
    caches: dict = {}
    rows, mats = [], []
    days = None
    for i, (sig, port) in enumerate(vs, 1):
        d, r, extra = run_variant(p, sig, port, costs, caches)
        days = d if days is None else days
        r = pd.Series(r, index=d).reindex(days).fillna(0.0).to_numpy()
        mats.append(r)
        is_m = _period(days, EVAL_START, HOLDOUT_START)
        ho_m = _period(days, HOLDOUT_START, HOLDOUT_END)
        ys = _yearly_sharpes(days, r, HOLDOUT_START)
        row = {**asdict(sig), **{k: v for k, v in asdict(port).items()}, **extra}
        row["lookbacks_d"] = "-".join(map(str, sig.lookbacks_d))
        row.update({f"is_{k}": v for k, v in summary(r[is_m]).items()})
        row.update({f"ho_{k}": v for k, v in summary(r[ho_m]).items()})
        row["is_min_year_sharpe"] = min(ys) if ys else np.nan
        row["is_median_year_sharpe"] = float(np.median(ys)) if ys else np.nan
        rows.append(row)
        if i % 12 == 0:
            logger.info("%d/%d variants", i, len(vs))
    df = pd.DataFrame(rows)
    m = np.column_stack(mats)
    is_m = _period(days, EVAL_START, HOLDOUT_START)
    m_is = m[is_m]
    sd = m_is.std(axis=0, ddof=1)
    sr_daily = np.divide(m_is.mean(axis=0), sd, out=np.zeros_like(sd), where=sd > 0)
    sr_var = float(sr_daily.var(ddof=1))
    df["is_dsr"] = [deflated_sharpe(m_is[:, k], len(vs), sr_var) for k in range(len(vs))]
    pbo = pbo_cscv(m_is)
    bm = benchmarks(p, days)
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "variants.csv", index=False)
    np.savez_compressed(out_dir / "daily_returns.npz", days=days, returns=m, **bm)
    logger.info("trials=%d  PBO=%.3f  sr_var(daily)=%.2e", len(vs), pbo, sr_var)
    for name, r in bm.items():
        logger.info("benchmark %-14s IS %s | HO %s", name, _fmt(summary(r[is_m])),
                    _fmt(summary(r[_period(days, HOLDOUT_START, HOLDOUT_END)])))
    (out_dir / "pbo.txt").write_text(f"{pbo:.4f}\n")
    return df


def _fmt(s: dict[str, float]) -> str:
    return f"SR {s['sharpe']:.2f} CAGR {s['cagr']:+.1%} DD {s['max_dd']:.1%}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", type=Path, default=PANEL_PATH)
    ap.add_argument("--out", type=Path, default=TREND_DIR / "results")
    ap.add_argument("--slippage", type=float, default=CostConfig().slippage)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    panel = shrink_to_universe(load_panel(args.panel), MAX_UNIVERSE)
    logger.info("panel: %d bars x %d symbols", len(panel.ts), len(panel.symbols))
    df = evaluate(panel, args.out, replace(CostConfig(), slippage=args.slippage))
    cols = ["kind", "lookbacks_d", "long_only", "regime_filter", "universe_size", "rebalance_every", "is_sharpe",
            "is_median_year_sharpe", "is_min_year_sharpe", "is_dsr", "is_cagr", "is_max_dd", "ho_sharpe",
            "ho_cagr", "ho_max_dd", "turnover_yr"]
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        logger.info("top 15 by IS median-year Sharpe:\n%s",
                    df.sort_values("is_median_year_sharpe", ascending=False)[cols].head(15).round(3))


if __name__ == "__main__":
    main()
