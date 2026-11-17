"""Paper engine for the multi-asset trend follower.

Runs once per closed 4h bar, mirroring the backtest step order:
  1. book funding settled since the previous run (holdings were constant)
  2. on rebalance bars: same signal / sizing code as the backtest -> target weights
  3. trade the banded difference at the current price (taker fee + slippage)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.trend.book import Book, apply_cash, apply_fill
from src.trend.config import BAR_S, BARS_PER_DAY, TrendSettings
from src.trend.data.download import EXCLUDE
from src.trend.portfolio import apply_band, realized_vol, target_weights, universe_mask
from src.trend.signals import compute_signal
from src.trend.storage import TrendStorage

logger = logging.getLogger(__name__)

SETTLE_DELAY_S = 20           # wait after the bar boundary so the kline is final
CANDIDATE_MULT = 2            # pre-select this many x universe_size by 24h volume
MAX_KLINES = 1500             # Binance per-request cap
KLINE_PAD = 10
WEIGHT_EPS = 1e-9
REGIME_CCXT_SYMBOL = "BTC/USDT:USDT"   # ccxt name of config.REGIME_SYMBOL


@dataclass(frozen=True)
class MarketState:
    bar_ts: int                       # open ts of the last closed bar
    symbols: tuple[str, ...]
    close: np.ndarray                 # (T, n)
    high: np.ndarray
    low: np.ndarray
    qvol: np.ndarray
    last: dict[str, float]            # current trade price per symbol


def kline_limit(settings: TrendSettings) -> int:
    p, s = settings.profile.portfolio, settings.profile.signal
    days = max(max(s.lookbacks_d) + 1, p.min_history_d, p.volume_window_d, p.vol_window_d)
    return min(days * BARS_PER_DAY + KLINE_PAD, MAX_KLINES)


def is_rebalance_bar(bar_ts: int, every: int) -> bool:
    return (bar_ts // BAR_S) % every == 0


def compute_targets(settings: TrendSettings, m: MarketState) -> dict[str, tuple[float, float, float]]:
    """symbol -> (signal, vol, target weight) for the last closed bar; same code path as the backtest."""
    prof = settings.profile
    regime_col = m.symbols.index(REGIME_CCXT_SYMBOL) if prof.signal.regime_filter else None
    sig = compute_signal(prof.signal, m.close, m.high, m.low, regime_col=regime_col)
    vol = realized_vol(m.close, prof.portfolio.vol_window_d * BARS_PER_DAY)
    uni = universe_mask(m.close, m.qvol, prof.portfolio)
    w = target_weights(sig[-1], vol[-1], uni[-1], prof.portfolio)
    return {s: (float(sig[-1, j]), float(vol[-1, j]), float(w[j])) for j, s in enumerate(m.symbols)}


class TrendEngine:
    def __init__(self, settings: TrendSettings, exchange, storage: TrendStorage, notifier,
                 clock=time) -> None:
        if not settings.is_paper:
            raise NotImplementedError("trend live trading is disabled until paper results validate it")
        self._s = settings
        self._ex = exchange
        self._db = storage
        self._notify = notifier
        self._clock = clock
        self._book = Book(settings.initial_capital)
        self._peak = settings.initial_capital
        self._halted = False

    # -- lifecycle -----------------------------------------------------------
    async def run(self) -> None:
        await self._ex.load_markets()
        await self._restore()
        await self._notify.alert(
            f"trend paper start: profile={self._s.profile.name} equity=${self._book.cash:,.2f} "
            f"holdings={len(self._book.holdings)}{' HALTED' if self._halted else ''}")
        last = await self._db.get_meta("last_bar_ts")
        if last is None:                                   # first start: position immediately
            await self.step(self._last_closed_bar(), force_rebalance=True)
        while True:
            await asyncio.sleep(self._seconds_to_next_bar())
            try:
                await self.step(self._last_closed_bar())
            except Exception:                              # keep the loop alive; next bar retries
                logger.exception("bar step failed")
                await self._notify.alert("trend step failed - check logs")

    async def _restore(self) -> None:
        self._book = await self._db.load_book(self._s.initial_capital)
        self._peak = await self._db.peak_equity() or self._book.cash
        self._halted = (await self._db.get_meta("halted")) == "1"

    def _last_closed_bar(self) -> int:
        now = int(self._clock.time())
        return (now // BAR_S) * BAR_S - BAR_S

    def _seconds_to_next_bar(self) -> float:
        now = self._clock.time()
        return max((now // BAR_S + 1) * BAR_S + SETTLE_DELAY_S - now, 1.0)

    # -- one bar -------------------------------------------------------------
    async def step(self, bar_ts: int, force_rebalance: bool = False) -> None:
        done = await self._db.get_meta("last_bar_ts")
        if done is not None and int(done) >= bar_ts and not force_rebalance:
            return
        market = await self._load_market(bar_ts)
        since = int(done) + BAR_S if done is not None else bar_ts + BAR_S
        await self._book_funding(since, market.last)
        marks = {**{s: h.entry_price for s, h in self._book.holdings.items()}, **market.last}
        rebalance = force_rebalance or is_rebalance_bar(bar_ts, self._s.profile.portfolio.rebalance_every)
        if rebalance and not self._halted:
            targets = compute_targets(self._s, market)
            await self._db.record_targets(bar_ts, [(s, *v) for s, v in targets.items() if v[0] or v[2]])
            await self._rebalance({s: v[2] for s, v in targets.items()}, marks, "rebalance")
        await self._check_drawdown(marks)
        await self._finish_bar(bar_ts, marks, rebalance)

    async def _load_market(self, bar_ts: int) -> MarketState:
        tickers = await self._ex.ccxt.fetch_tickers()
        ranked = sorted(
            ((float(t.get("quoteVolume") or 0), s) for s, t in tickers.items()
             if self._ex.has_spec(s) and self._ex.spec(s).ws_id.upper() not in EXCLUDE),
            reverse=True)
        n = self._s.profile.portfolio.universe_size * CANDIDATE_MULT
        cands = [s for _, s in ranked[:n]]
        extra = [*self._book.holdings, REGIME_CCXT_SYMBOL]          # BTC always loaded for the regime filter
        cands += [s for s in dict.fromkeys(extra) if s not in cands and self._ex.has_spec(s)]
        limit = kline_limit(self._s)
        frames = await asyncio.gather(*(self._klines(s, bar_ts, limit) for s in cands))
        cols = {s: f for s, f in zip(cands, frames, strict=True) if f is not None}
        idx = np.arange(bar_ts - (limit - 1) * BAR_S, bar_ts + 1, BAR_S)
        syms = tuple(cols)

        def stack(field: str) -> np.ndarray:
            return np.column_stack([cols[s][field].reindex(idx).to_numpy(dtype=np.float32) for s in syms])

        last = {s: float(tickers[s]["last"]) for s in syms if tickers.get(s, {}).get("last")}
        for s in self._book.holdings:                       # delisted holdings: best known price
            if s not in last and s in tickers and tickers[s].get("last"):
                last[s] = float(tickers[s]["last"])
        return MarketState(bar_ts, syms, stack("close"), stack("high"), stack("low"), stack("qvol"), last)

    async def _klines(self, symbol: str, bar_ts: int, limit: int) -> pd.DataFrame | None:
        since_ms = (bar_ts - (limit - 1) * BAR_S) * 1000
        try:
            rows = await self._ex._call(self._ex.ccxt.fetch_ohlcv, symbol, "4h", since_ms, limit)
        except Exception as e:                              # one bad symbol must not stop the bar
            logger.warning("klines %s failed: %s", symbol, e)
            return None
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = df["ts"] // 1000
        df = df[df["ts"] <= bar_ts].set_index("ts")        # drop the still-open bar
        df["qvol"] = df["volume"] * df["close"]
        return df

    async def _book_funding(self, since_ts: int, marks: dict[str, float]) -> None:
        """Charge settlements in [since_ts, now] to the holdings that were open during them."""
        now = self._clock.time()
        for sym, h in list(self._book.holdings.items()):
            try:
                rows = await self._ex._call(self._ex.ccxt.fetch_funding_rate_history, sym, since_ts * 1000)
            except Exception as e:
                logger.warning("funding %s failed: %s", sym, e)
                continue
            for r in rows:
                ts = float(r["timestamp"]) / 1000
                if ts < since_ts or ts > now:
                    continue
                rate = float(r["fundingRate"])
                mark = float((r.get("info") or {}).get("markPrice") or 0) or marks.get(sym, h.entry_price)
                amount = -h.qty * mark * rate
                if await self._db.record_funding(ts, sym, rate, amount):
                    self._book = apply_cash(self._book, amount)
        await self._db.save_book(self._book)

    async def _rebalance(self, targets: dict[str, float], marks: dict[str, float], reason: str) -> None:
        equity = self._book.equity(marks)
        if equity <= 0:
            logger.error("non-positive equity %.2f, skipping rebalance", equity)
            return
        current = self._book.weights(marks)
        for sym in sorted(set(targets) | set(current)):
            if sym not in marks:
                logger.warning("no price for %s, cannot trade", sym)
                continue
            cur_w = current.get(sym, 0.0)
            tgt_w = targets.get(sym, 0.0)
            new_w = float(apply_band(np.array([cur_w]), np.array([tgt_w]), self._s.profile.portfolio.rebalance_band)[0])
            if abs(new_w - cur_w) < WEIGHT_EPS:
                continue
            await self._trade(sym, new_w, equity, marks[sym], reason)

    async def _trade(self, sym: str, new_w: float, equity: float, price: float, reason: str) -> None:
        held = self._book.holdings.get(sym)
        cur_qty = held.qty if held else 0.0
        tgt_qty = 0.0 if new_w == 0 else self._round_qty(sym, new_w * equity / price)
        dqty = tgt_qty - cur_qty
        if abs(dqty) * price < self._min_notional(sym) and tgt_qty != 0:
            return                                          # too small to place on the exchange
        if dqty == 0:
            return
        costs = self._s.profile.costs
        fill = price * (1 + costs.slippage) if dqty > 0 else price * (1 - costs.slippage)
        fee = abs(dqty) * fill * costs.fee
        self._book, realized = apply_fill(self._book, sym, dqty, fill, fee)
        await self._db.record_fill(self._clock.time(), sym, dqty, fill, fee, realized, new_w, reason)
        await self._db.save_book(self._book)
        logger.info("%s %s %+.6g @ %.6g (w %.3f) fee %.4f", reason, sym, dqty, fill, new_w, fee)

    def _round_qty(self, sym: str, qty: float) -> float:
        step = self._ex.spec(sym).qty_step if self._ex.has_spec(sym) else 0.0
        return float(np.round(qty / step) * step) if step > 0 else qty

    def _min_notional(self, sym: str) -> float:
        spec_min = self._ex.spec(sym).min_notional if self._ex.has_spec(sym) else 0.0
        return max(spec_min, self._s.risk.min_notional_usdt)

    async def _check_drawdown(self, marks: dict[str, float]) -> None:
        equity = self._book.equity(marks)
        self._peak = max(self._peak, equity)
        dd = 1 - equity / self._peak if self._peak > 0 else 0.0
        if dd >= self._s.risk.max_drawdown and not self._halted:
            await self._rebalance({}, marks, "drawdown_halt")
            self._halted = True
            await self._db.set_meta("halted", "1")
            await self._notify.alert(f"trend HALT: drawdown {dd:.1%} >= {self._s.risk.max_drawdown:.0%}, flattened")

    async def _finish_bar(self, bar_ts: int, marks: dict[str, float], rebalanced: bool) -> None:
        equity = self._book.equity(marks)
        gross = self._book.gross(marks)
        await self._db.snapshot_equity(self._clock.time(), equity, self._book.cash, gross,
                                       len(self._book.holdings), self._peak)
        await self._db.set_meta("last_bar_ts", str(bar_ts))
        logger.info("bar %s equity $%.2f gross %.2f positions %d%s",
                    pd.Timestamp(bar_ts, unit="s"), equity, gross, len(self._book.holdings),
                    " (rebalanced)" if rebalanced else "")
        if rebalanced:
            top = sorted(self._book.weights(marks).items(), key=lambda kv: -abs(kv[1]))[:8]
            pos = ", ".join(f"{s.split('/')[0]} {w:+.2f}" for s, w in top) or "flat"
            await self._notify.report(f"equity ${equity:,.2f} (peak ${self._peak:,.2f}) gross {gross:.2f} | {pos}")
