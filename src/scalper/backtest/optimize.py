"""Robust parameter search: time-fold cross validation + untouched holdout.

Selection uses folds 0..K-2 (score = worst-fold mean R, so a config must
work in every period); the last fold is a holdout reported but never used
for selection.

Usage:
    python -m src.scalper.backtest.optimize --data DIR --symbols A,B --out results.csv
"""

from __future__ import annotations

import argparse
import itertools
import logging
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

from src.scalper.backtest.data import load_symbol
from src.scalper.backtest.simulator import ExecAssumptions, build_features, simulate
from src.scalper.config import StrategyConfig

logger = logging.getLogger(__name__)

GRID: dict[str, list] = {
    "lookback_s": [60, 180, 300],
    "z_entry": [3.0, 4.0, 5.0],
    "burst_min": [0.0, 3.0],
    "min_move_bps": [20.0, 40.0, 60.0],
    "tp_frac": [0.3, 0.5, 0.7],
    "sl_frac": [0.5, 1.0],
    "max_hold_s": [120, 300, 600],
    "maker_entry": [False, True],
}
N_FOLDS = 4
AGG_FIELDS = ("n", "sum_bps", "sum_r", "wins", "win_bps", "loss_bps")


def combos(grid: dict[str, list]) -> list[StrategyConfig]:
    keys = list(grid)
    return [replace(StrategyConfig(), **dict(zip(keys, v, strict=True))) for v in itertools.product(*grid.values())]


def _aggregate(df: pd.DataFrame, fold_edges: np.ndarray) -> np.ndarray:
    """(N_FOLDS, len(AGG_FIELDS)) sufficient statistics."""
    out = np.zeros((N_FOLDS, len(AGG_FIELDS)))
    if df.empty:
        return out
    fold = np.clip(np.searchsorted(fold_edges, df["ts"].to_numpy(), side="right") - 1, 0, N_FOLDS - 1)
    net = df["net_bps"].to_numpy()
    r = df["r"].to_numpy()
    for k in range(N_FOLDS):
        m = fold == k
        nk = net[m]
        out[k] = (m.sum(), nk.sum(), r[m].sum(), (nk > 0).sum(), nk[nk > 0].sum(), -nk[nk <= 0].sum())
    return out


def _run_symbol(args: tuple) -> np.ndarray:
    data_dir, cache_dir, symbol, configs, fold_edges, ex = args
    bars = load_symbol(Path(data_dir), Path(cache_dir), symbol)
    feats: dict[int, object] = {}
    result = np.zeros((len(configs), N_FOLDS, len(AGG_FIELDS)))
    for ci, cfg in enumerate(configs):
        if cfg.lookback_s not in feats:
            feats[cfg.lookback_s] = build_features(bars, cfg.lookback_s, cfg.sigma_window_s)
        result[ci] = _aggregate(simulate(feats[cfg.lookback_s], cfg, ex), fold_edges)
    logger.info("done %s", symbol)
    return result


def fold_edges_for(data_dir: Path, symbols: list[str]) -> np.ndarray:
    days = sorted({p.stem.split("-aggTrades-")[1] for s in symbols for p in data_dir.glob(f"{s}-aggTrades-*.zip")})
    start = pd.Timestamp(days[0], tz="UTC").timestamp()
    end = (pd.Timestamp(days[-1], tz="UTC") + pd.Timedelta(days=1)).timestamp()
    return np.linspace(start, end, N_FOLDS + 1)


def summarize(configs: list[StrategyConfig], agg: np.ndarray) -> pd.DataFrame:
    rows = []
    for cfg, a in zip(configs, agg, strict=True):
        row = asdict(cfg)
        for k in range(N_FOLDS):
            n, sb, sr, w, wb, lb = a[k]
            row[f"f{k}_n"] = int(n)
            row[f"f{k}_bps"] = sb / n if n else np.nan
            row[f"f{k}_r"] = sr / n if n else np.nan
            row[f"f{k}_wr"] = w / n if n else np.nan
            row[f"f{k}_pf"] = wb / lb if lb else np.nan
        sel = a[: N_FOLDS - 1].sum(axis=0)
        row["sel_n"] = int(sel[0])
        row["sel_r"] = sel[2] / sel[0] if sel[0] else np.nan
        row["sel_bps"] = sel[1] / sel[0] if sel[0] else np.nan
        row["sel_min_fold_r"] = np.nanmin([row[f"f{k}_r"] for k in range(N_FOLDS - 1)])
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    symbols = args.symbols.split(",")
    cache = args.cache or args.data.parent / "cache"
    configs = combos(GRID)
    edges = fold_edges_for(args.data, symbols)
    ex = ExecAssumptions()
    logger.info("%d configs x %d symbols, folds=%s", len(configs), len(symbols),
                [pd.Timestamp(e, unit="s").date() for e in edges])
    jobs = [(str(args.data), str(cache), s, configs, edges, ex) for s in symbols]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        total = sum(pool.map(_run_symbol, jobs))
    res = summarize(configs, total)
    res.to_csv(args.out, index=False)
    logger.info("wrote %s", args.out)


if __name__ == "__main__":
    main()
