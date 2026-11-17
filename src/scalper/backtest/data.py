"""aggTrades zip -> 1s bars (close, high, low, buy_notional, sell_notional), cached as npz."""
from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd


def _day_bars(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(zf.namelist()[0]) as f:
            df = pd.read_csv(
                f, usecols=["price", "quantity", "transact_time", "is_buyer_maker"],
                dtype={"price": "float64", "quantity": "float64", "transact_time": "int64"},
            )
    maker = df["is_buyer_maker"].astype(str).str.lower().eq("true").to_numpy()
    sec = (df["transact_time"].to_numpy() // 1000)
    notional = df["price"].to_numpy() * df["quantity"].to_numpy()
    tmp = pd.DataFrame({
        "sec": sec, "price": df["price"].to_numpy(),
        "buy": np.where(maker, 0.0, notional), "sell": np.where(maker, notional, 0.0),
    })
    return tmp.groupby("sec").agg(
        close=("price", "last"), high=("price", "max"), low=("price", "min"),
        buy=("buy", "sum"), sell=("sell", "sum"),
    )


def load_symbol(data_dir: Path, cache_dir: Path, symbol: str) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for z in sorted(data_dir.glob(f"{symbol}-aggTrades-*.zip")):
        c = cache_dir / (z.stem + ".npz")
        if c.exists():
            a = np.load(c)
            frames.append(pd.DataFrame({k: a[k] for k in ("close", "high", "low", "buy", "sell")}, index=a["sec"]))
            continue
        try:
            d = _day_bars(z)
        except (zipfile.BadZipFile, ValueError, KeyError):
            continue
        np.savez(c, sec=d.index.to_numpy(), **{k: d[k].to_numpy() for k in d.columns})
        frames.append(d)
    if not frames:
        raise FileNotFoundError(symbol)
    bars = pd.concat(frames)
    bars = bars[~bars.index.duplicated()]
    idx = pd.RangeIndex(bars.index.min(), bars.index.max() + 1)
    bars = bars.reindex(idx)
    bars["close"] = bars["close"].ffill()
    bars["high"] = bars["high"].fillna(bars["close"])
    bars["low"] = bars["low"].fillna(bars["close"])
    bars[["buy", "sell"]] = bars[["buy", "sell"]].fillna(0.0)
    return bars
