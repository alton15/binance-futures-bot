"""Deterministic fake exchange for trend engine tests (no network)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.scalper.execution.exchange import MarketSpec
from src.trend.config import BAR_S

N_BARS = 700
FUNDING_RATE = 0.0001


@dataclass
class FakeClock:
    now: float

    def time(self) -> float:
        return self.now


def make_prices(symbols: tuple[str, ...], drifts: tuple[float, ...], seed: int = 7) -> dict[str, np.ndarray]:
    """N_BARS closed bars + 1 still-open bar of log-drift + noise closes per symbol."""
    rng = np.random.default_rng(seed)
    out = {}
    for s, d in zip(symbols, drifts, strict=True):
        steps = d + rng.normal(0.0, 0.01, N_BARS + 1)
        out[s] = 100.0 * np.exp(np.cumsum(steps))
    return out


@dataclass
class _Ccxt:
    parent: FakeExchange

    async def fetch_tickers(self) -> dict:
        p = self.parent
        return {s: {"last": float(p.closes[s][-1]) * p.shock.get(s, 1.0), "quoteVolume": p.qvol[s]}
                for s in p.closes}

    async def fetch_ohlcv(self, symbol: str, timeframe: str, since_ms: int, limit: int) -> list[list[float]]:
        p = self.parent
        assert timeframe == "4h"
        rows = []
        for k, ts in enumerate(p.bar_ts):
            if ts * 1000 < since_ms:
                continue
            c = float(p.closes[symbol][k])
            rows.append([ts * 1000, c, c * 1.005, c * 0.995, c, p.qvol[symbol] / 6 / c])
        return rows[:limit]

    async def fetch_funding_rate_history(self, symbol: str, since_ms: int) -> list[dict]:
        p = self.parent
        return [{"timestamp": ts * 1000, "fundingRate": FUNDING_RATE, "info": {"markPrice": "0"}}
                for ts in p.funding_ts if ts * 1000 >= since_ms]


@dataclass
class FakeExchange:
    closes: dict[str, np.ndarray]
    qvol: dict[str, float]
    bar_ts: np.ndarray                               # open ts per close index (last = still-open bar)
    funding_ts: list[int] = field(default_factory=list)
    shock: dict[str, float] = field(default_factory=dict)   # multiplies ticker last (drawdown tests)

    def __post_init__(self) -> None:
        self.ccxt = _Ccxt(self)

    async def load_markets(self) -> None:
        return None

    async def _call(self, fn, *args, **kwargs):
        return await fn(*args, **kwargs)

    def has_spec(self, symbol: str) -> bool:
        return symbol in self.closes

    def spec(self, symbol: str) -> MarketSpec:
        ws = symbol.split("/")[0].lower() + "usdt"
        return MarketSpec(symbol, ws, qty_step=0.0001, tick_size=0.0001, min_notional=5.0)


def build_exchange(symbols: tuple[str, ...], drifts: tuple[float, ...], last_closed: int) -> FakeExchange:
    closes = make_prices(symbols, drifts)
    bar_ts = np.arange(last_closed - (N_BARS - 1) * BAR_S, last_closed + 2 * BAR_S, BAR_S)
    qvol = {s: 1e9 / (i + 1) for i, s in enumerate(symbols)}
    return FakeExchange(closes, qvol, bar_ts)


class NullNotifier:
    def __init__(self) -> None:
        self.alerts: list[str] = []
        self.reports: list[str] = []

    async def alert(self, text: str) -> None:
        self.alerts.append(text)

    async def report(self, text: str) -> None:
        self.reports.append(text)
