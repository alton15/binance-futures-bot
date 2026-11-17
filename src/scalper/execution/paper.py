"""Paper broker with fill rules mirroring the backtest simulator.

- maker entry: post-only limit at signal price; rejected if it would cross the
  book; fills only when a trade prints *through* the limit within maker_wait_s
- taker entry: fills on the first trade after the latency delay, at the
  current ask (long) / bid (short), taker fee
- TP: resting limit, fills when a trade prints through it (maker fee)
- SL: triggers on last trade price; fills at the worse of stop and the current
  book side, minus slippage (taker fee)
- time stop: taker at the current book side (deferred until a price is known)
- quotes older than `max_quote_age_s` are ignored; the last trade price is
  used instead (never the entry price)
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, replace

from src.scalper.config import MAKER_FEE, TAKER_FEE, StrategyConfig
from src.scalper.risk.sizing import STOP_SLIPPAGE, SizeResult
from src.scalper.strategy.fade import bracket
from src.scalper.types import ClosedTrade, ExitReason, Position, Quote, Side, Signal

logger = logging.getLogger(__name__)

TAKER_FALLBACK_SPREAD = 0.0001  # used only when no fresh quote is available
DEFAULT_MAX_QUOTE_AGE_S = 10.0


@dataclass(frozen=True)
class PendingEntry:
    signal: Signal
    size: SizeResult
    limit_price: float
    active_from: float
    expires_at: float


class PaperBroker:
    """Event-driven simulated exchange. Pure state machine: feed it trades,
    quotes and clock ticks; it returns fills/closes for the engine to record."""

    def __init__(self, cfg: StrategyConfig, latency_s: float,
                 max_quote_age_s: float = DEFAULT_MAX_QUOTE_AGE_S) -> None:
        self._cfg = cfg
        self._latency = latency_s
        self._max_quote_age_s = max_quote_age_s
        self._ids = itertools.count(1)
        self.pending: dict[str, PendingEntry] = {}
        self.positions: dict[str, Position] = {}
        self._quotes: dict[str, Quote] = {}
        self._last_trade: dict[str, float] = {}

    def on_quote(self, q: Quote) -> None:
        self._quotes[q.symbol] = q

    def _fresh_quote(self, symbol: str, now: float) -> Quote | None:
        q = self._quotes.get(symbol)
        if q is None or now - q.ts_ms / 1000.0 > self._max_quote_age_s:
            return None
        return q

    def submit_entry(self, signal: Signal, size: SizeResult, now: float) -> str:
        """Returns '' if accepted, else rejection reason."""
        q = self._quotes.get(signal.symbol)
        px = signal.price
        if q is not None and self._cfg.maker_entry:
            crosses = (signal.side is Side.LONG and q.ask <= px) or (signal.side is Side.SHORT and q.bid >= px)
            if crosses:
                return "post-only would cross"
        self.pending[signal.symbol] = PendingEntry(
            signal, size, px, now + self._latency, now + self._latency + self._cfg.maker_wait_s,
        )
        return ""

    def on_trade(self, symbol: str, price: float, now: float) -> tuple[Position | None, ClosedTrade | None]:
        self._last_trade[symbol] = price
        opened = self._try_fill_entry(symbol, price, now)
        closed = self._check_exit(symbol, price, now)
        return opened, closed

    def _try_fill_entry(self, symbol: str, price: float, now: float) -> Position | None:
        pe = self.pending.get(symbol)
        if pe is None or now < pe.active_from or now >= pe.expires_at:
            return None  # expired entries are removed by on_clock
        side = pe.signal.side
        if self._cfg.maker_entry:
            through = price < pe.limit_price if side is Side.LONG else price > pe.limit_price
            if not through:
                return None
            entry, fee_rate = pe.limit_price, MAKER_FEE
        else:
            q = self._fresh_quote(symbol, now)
            book = (q.ask if side is Side.LONG else q.bid) if q else price * (1 + int(side) * TAKER_FALLBACK_SPREAD)
            entry, fee_rate = book, TAKER_FEE
        del self.pending[symbol]
        tp, sl = bracket(entry, side, pe.signal.move_bps, self._cfg)
        pos = Position(
            id=next(self._ids), symbol=symbol, side=side, qty=pe.size.qty, entry_price=entry,
            tp_price=tp, sl_price=sl, opened_ts=now, entry_fee=entry * pe.size.qty * fee_rate,
            leverage=pe.size.leverage,
        )
        self.positions[symbol] = pos
        return pos

    def _check_exit(self, symbol: str, price: float, now: float) -> ClosedTrade | None:
        pos = self.positions.get(symbol)
        if pos is None or now <= pos.opened_ts:
            return None
        d = int(pos.side)
        if (d > 0 and price <= pos.sl_price) or (d < 0 and price >= pos.sl_price):
            book = self._book_exit_price(pos, price, now)
            worse = min(pos.sl_price, book) if d > 0 else max(pos.sl_price, book)
            return self._close(pos, worse * (1 - d * STOP_SLIPPAGE), TAKER_FEE, now, ExitReason.STOP_LOSS)
        if (d > 0 and price > pos.tp_price) or (d < 0 and price < pos.tp_price):
            return self._close(pos, pos.tp_price, MAKER_FEE, now, ExitReason.TAKE_PROFIT)
        return None

    def on_clock(self, now: float) -> tuple[list[str], list[ClosedTrade]]:
        """Expire stale entries and apply time stops. Returns (expired symbols, closes)."""
        expired = [s for s, pe in self.pending.items() if now >= pe.expires_at]
        for s in expired:
            del self.pending[s]
        closes = []
        for pos in list(self.positions.values()):
            if now - pos.opened_ts >= self._cfg.max_hold_s:
                closes.append(self.close_now(pos.symbol, now, ExitReason.TIME_STOP))
        return expired, [c for c in closes if c is not None]

    def close_now(self, symbol: str, now: float, reason: ExitReason) -> ClosedTrade | None:
        """Taker exit at the book; returns None (defer) if no price is known yet."""
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        last = self._last_trade.get(symbol)
        if self._fresh_quote(symbol, now) is None and last is None:
            logger.warning("no price for %s yet, deferring %s", symbol, reason.value)
            return None
        return self._close(pos, self._book_exit_price(pos, last, now), TAKER_FEE, now, reason)

    def _book_exit_price(self, pos: Position, last: float, now: float) -> float:
        q = self._fresh_quote(pos.symbol, now)
        if q is None:
            return last
        return q.bid if pos.side is Side.LONG else q.ask

    def _close(self, pos: Position, px: float, fee_rate: float, now: float,
               reason: ExitReason) -> ClosedTrade:
        del self.positions[pos.symbol]
        return ClosedTrade(pos, px, px * pos.qty * fee_rate, now, reason)

    def restore(self, pos: Position) -> None:
        """Re-adopt a persisted open position after restart."""
        self.positions[pos.symbol] = replace(pos)
