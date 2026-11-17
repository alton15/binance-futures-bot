"""Paper-trading engine: market streams -> bars -> signals -> risk -> broker.

All market handlers are synchronous and fast; persistence/notifications run
as tracked background tasks so a slow disk or webhook never stalls the feed.
Those tasks are drained on shutdown before storage closes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Coroutine

from src.scalper.config import Settings
from src.scalper.execution.exchange import Exchange
from src.scalper.execution.paper import PaperBroker
from src.scalper.market.bars import BarBuilder
from src.scalper.market.ws import MARKET_URL, PUBLIC_URL, StreamClient
from src.scalper.notify import Notifier
from src.scalper.risk.guard import RiskGuard
from src.scalper.risk.sizing import size_position
from src.scalper.storage import Storage, position_meta
from src.scalper.strategy.fade import evaluate
from src.scalper.strategy.features import SymbolFeatures
from src.scalper.types import Bar, ClosedTrade, Position, Quote, Signal

logger = logging.getLogger(__name__)

RECONNECT_ENTRY_BLOCK_S = 60.0
CLOCK_INTERVAL_S = 1.0
MAX_SIGNAL_AGE_S = 5.0
LOOP_LAG_WARN_S = 0.5
SIGNAL_LOG_DEDUPE_S = 60.0     # an extreme move fires on every bar; persist one per minute
DRAIN_TIMEOUT_S = 10.0


def _utc_midnight(ts: float) -> float:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class Engine:
    def __init__(self, settings: Settings, exchange: Exchange, storage: Storage, notifier: Notifier) -> None:
        if not settings.is_paper:
            raise NotImplementedError("Live mode is disabled until a forward paper test shows an edge")
        self._s = settings
        self._cfg = settings.strategy
        self._ex = exchange
        self._db = storage
        self._notify = notifier
        self._broker = PaperBroker(self._cfg, settings.ops.paper_latency_s, settings.risk.max_quote_age_s)
        self._guard: RiskGuard | None = None
        self._builders: dict[str, BarBuilder] = {}
        self._features: dict[str, SymbolFeatures] = {}
        self._quotes: dict[str, Quote] = {}
        self._open_rows: dict[str, asyncio.Future[int]] = {}  # symbol -> positions.id (resolves on insert)
        self._pending_meta: dict[str, dict] = {}   # symbol -> signal meta until fill
        self._last_signal_log: dict[str, float] = {}
        self._universe: list[str] = []
        self._tasks: set[asyncio.Task] = set()
        self._entry_block_until = 0.0
        self._market = StreamClient(MARKET_URL, self._on_market, "market", settings.ops.stale_stream_s,
                                    settings.ops.max_conn_age_s)
        self._public = StreamClient(PUBLIC_URL, self._on_public, "public", settings.ops.stale_stream_s,
                                    settings.ops.max_conn_age_s)
        self._seen_reconnects = 0
        self.max_loop_lag = 0.0

    # -- lifecycle -----------------------------------------------------------
    async def run(self) -> None:
        await self._ex.load_markets()
        await self._restore_state()
        await self._refresh_universe()
        self._market.start()
        self._public.start()
        g = self._guard
        await self._notify.alert(
            f"started paper engine: equity ${g.equity:.2f}, {len(self._universe)} symbols, "
            f"mode={'follow' if self._cfg.follow else 'fade'} (warmup ~{self._cfg.sigma_window_s // 2 // 60}m)"
            + (f" | HALTED: {g.halted_reason}" if g.halted_reason else "")
        )
        loops = [self._clock_loop(), self._universe_loop(), self._status_loop()]
        try:
            await asyncio.gather(*(asyncio.create_task(c) for c in loops))
        finally:
            logger.info("stopping streams")
            await asyncio.gather(self._market.stop(), self._public.stop())
            await self._drain()
            logger.info("engine stopped")

    async def _restore_state(self) -> None:
        now = time.time()
        equity = self._s.initial_capital + await self._db.realized_pnl()
        day_start = equity - await self._db.realized_pnl(since_ts=_utc_midnight(now))
        self._guard = RiskGuard(self._s.risk, equity, await self._db.peak_equity(), now=now,
                                day_start_equity=day_start)
        for db_id, pos in await self._db.open_positions():
            self._broker.restore(pos)
            row: asyncio.Future[int] = asyncio.get_running_loop().create_future()
            row.set_result(db_id)
            self._open_rows[pos.symbol] = row
            self._guard.reserve(pos.symbol)
            logger.info("restored open paper position %s %s", pos.symbol, pos.side.name)

    async def _drain(self) -> None:
        """Wait for pending DB/notify work so fills and closes are not lost."""
        pending = [*self._tasks, *(f for f in self._open_rows.values() if not f.done())]
        if not pending:
            return
        logger.info("draining %d background tasks", len(pending))
        done, not_done = await asyncio.wait(pending, timeout=DRAIN_TIMEOUT_S)
        if not_done:
            logger.error("%d background tasks did not finish before shutdown", len(not_done))

    def _spawn(self, coro: Coroutine) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("background task failed", exc_info=task.exception())

    # -- universe ------------------------------------------------------------
    async def _refresh_universe(self) -> None:
        u = self._s.universe
        top = await self._ex.top_symbols(u.min_quote_volume_24h, u.max_symbols, u.exclude)
        keep = set(self._broker.positions) | set(self._broker.pending)
        wanted = set(top) | keep
        missing = {s for s in wanted if not self._ex.has_spec(s)}
        for s in missing:
            logger.error("no market spec for %s (delisted?) - cannot price it", s)
        self._universe = sorted(wanted - missing)
        for gone in set(self._builders) - set(self._universe):
            # drop state so a symbol that re-enters later starts with a fresh warmup
            self._builders.pop(gone, None)
            self._features.pop(gone, None)
        ws = {self._ex.spec(s).ws_id for s in self._universe}
        await self._market.set_streams({f"{w}@aggTrade" for w in ws})
        await self._public.set_streams({f"{w}@bookTicker" for w in ws})
        for s in self._universe:
            self._builders.setdefault(s, BarBuilder(s))
            self._features.setdefault(s, SymbolFeatures(self._cfg.lookback_s, self._cfg.sigma_window_s))
        logger.info("universe: %d symbols", len(self._universe))

    async def _universe_loop(self) -> None:
        while True:
            await asyncio.sleep(self._s.universe.refresh_s)
            try:
                await self._refresh_universe()
            except Exception:  # noqa: BLE001 - keep trading on the old universe
                logger.exception("universe refresh failed")

    # -- market handlers (sync, fast) ----------------------------------------
    def _on_public(self, stream: str, d: dict) -> None:
        sym = self._ex.symbol_for_ws(d.get("s", ""))
        if sym is None:
            return
        q = Quote(sym, float(d["b"]), float(d["a"]), int(d.get("T") or d.get("E") or 0))
        self._quotes[sym] = q
        self._broker.on_quote(q)

    def _on_market(self, stream: str, d: dict) -> None:
        if d.get("e") != "aggTrade":
            return
        sym = self._ex.symbol_for_ws(d.get("s", ""))
        builder = self._builders.get(sym) if sym else None
        if builder is None:
            return
        price = float(d["p"])
        opened, closed = self._broker.on_trade(sym, price, time.time())
        if opened:
            self._on_opened(opened)
        if closed:
            self._on_closed(closed)
        for bar in builder.on_trade(price, float(d["q"]), int(d["T"]), bool(d["m"])):
            self._on_bar(sym, bar)

    def _on_bar(self, sym: str, bar: Bar) -> None:
        features = self._features.get(sym)
        if features is None:
            return
        snap = features.push(bar)
        if snap is None:
            return
        sig = evaluate(sym, snap, self._cfg)
        if sig is not None:
            self._on_signal(sig)

    def _on_signal(self, sig: Signal) -> None:
        now = time.time()
        outcome = self._try_enter(sig, now)
        submitted = outcome.startswith("submitted")
        if not submitted and now - self._last_signal_log.get(sig.symbol, 0.0) < SIGNAL_LOG_DEDUPE_S:
            return
        self._last_signal_log[sig.symbol] = now
        logger.info("signal %s %s z=%.2f move=%.0fbps -> %s", sig.symbol, sig.side.name, sig.z, sig.move_bps,
                    outcome)
        self._spawn(self._db.log_signal(sig, outcome))

    def _try_enter(self, sig: Signal, now: float) -> str:
        if now - sig.ts > MAX_SIGNAL_AGE_S:
            return "stale bar"
        if now < self._entry_block_until:
            return "blocked after reconnect"
        decision = self._guard.can_enter(sig.symbol, now)
        if not decision.allowed:
            return decision.reason
        q = self._quotes.get(sig.symbol)
        if q is None or now - q.ts_ms / 1000.0 > self._s.risk.max_quote_age_s:
            return "no fresh quote"
        spread_bps = (q.ask - q.bid) / ((q.ask + q.bid) / 2) * 1e4
        if spread_bps > self._s.risk.max_spread_bps:
            return f"spread {spread_bps:.1f}bps"
        spec = self._ex.spec(sig.symbol)
        size = size_position(self._guard.equity, sig.price, sig.sl_price, self._cfg.maker_entry,
                             self._s.risk, spec.qty_step, spec.min_notional)
        if not size.ok:
            return f"size: {size.reason}"
        reject = self._broker.submit_entry(sig, size, now)
        if reject:
            return reject
        self._guard.reserve(sig.symbol)
        self._pending_meta[sig.symbol] = position_meta(sig)
        return f"submitted qty={size.qty:g} lev={size.leverage}x notional=${size.notional:.0f}"

    # -- fills -----------------------------------------------------------------
    def _on_opened(self, pos: Position) -> None:
        meta = self._pending_meta.pop(pos.symbol, None)
        logger.info("OPEN %s %s qty=%g @ %.6g tp=%.6g sl=%.6g", pos.symbol, pos.side.name, pos.qty,
                    pos.entry_price, pos.tp_price, pos.sl_price)
        row = asyncio.ensure_future(self._db.open_position(pos, meta))
        self._open_rows[pos.symbol] = row
        self._spawn(self._notify_open(pos, row))

    async def _notify_open(self, pos: Position, row: asyncio.Future[int]) -> None:
        try:
            await row
        except Exception:  # noqa: BLE001 - close path re-inserts; just report
            logger.exception("failed to persist open %s", pos.symbol)
            return
        await self._notify.alert(f"OPEN {pos.side.name} {pos.symbol} @ {pos.entry_price:.6g} "
                                 f"(tp {pos.tp_price:.6g} / sl {pos.sl_price:.6g}, {pos.leverage}x)")

    def _on_closed(self, t: ClosedTrade) -> None:
        now = time.time()
        g = self._guard
        g.record_pnl(t.net_pnl, now)
        g.release(t.position.symbol, now, self._cfg.cooldown_s)
        logger.info("CLOSE %s %s %s net=$%.4f equity=$%.2f", t.position.symbol, t.position.side.name,
                    t.reason.value, t.net_pnl, g.equity)
        # take the row future synchronously so a re-open of the symbol can't race it
        row = self._open_rows.pop(t.position.symbol, None)
        self._spawn(self._persist_close(t, row, g.equity, g.peak, g.halted_reason))

    async def _persist_close(self, t: ClosedTrade, row: asyncio.Future[int] | None, equity: float,
                             peak: float, halted: str) -> None:
        db_id: int | None = None
        if row is not None:
            try:
                db_id = await row
            except Exception:  # noqa: BLE001 - fall through to recovery insert
                logger.exception("open row for %s missing, recording closed trade directly", t.position.symbol)
        if db_id is None:
            await self._db.record_closed(t)
        else:
            await self._db.close_position(db_id, t)
        await self._db.snapshot_equity(t.closed_ts, equity, peak)   # persists peak for restarts
        await self._notify.alert(f"CLOSE {t.position.symbol} {t.reason.value} net ${t.net_pnl:+.4f} "
                                 f"| equity ${equity:.2f}")
        if halted:
            await self._notify.alert(f"HALT: {halted}")

    # -- periodic --------------------------------------------------------------
    async def _clock_loop(self) -> None:
        while True:
            t0 = time.monotonic()
            await asyncio.sleep(CLOCK_INTERVAL_S)
            lag = time.monotonic() - t0 - CLOCK_INTERVAL_S
            self.max_loop_lag = max(self.max_loop_lag, lag)
            if lag > LOOP_LAG_WARN_S:
                logger.warning("event loop lag %.2fs", lag)
            try:
                self._tick(time.time())
            except Exception:  # noqa: BLE001 - one bad tick must not stop the engine
                logger.exception("clock tick failed")

    def _tick(self, now: float) -> None:
        reconnects = self._market.reconnects + self._public.reconnects
        if reconnects != self._seen_reconnects:
            self._seen_reconnects = reconnects
            self._entry_block_until = now + RECONNECT_ENTRY_BLOCK_S
        for sym, b in list(self._builders.items()):
            try:
                for bar in b.flush(int(now) - 1):
                    self._on_bar(sym, bar)
            except Exception:  # noqa: BLE001 - isolate per-symbol failures
                logger.exception("bar flush failed for %s", sym)
        expired, closes = self._broker.on_clock(now)
        for sym in expired:
            self._guard.release(sym, now, 0)
            self._pending_meta.pop(sym, None)
            logger.info("entry expired unfilled: %s", sym)
        for c in closes:
            self._on_closed(c)

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(self._s.ops.status_interval_s)
            try:
                g = self._guard
                await self._db.snapshot_equity(time.time(), g.equity, g.peak)
                st = await self._db.stats()
                age = time.monotonic() - self._market.last_message_at
                await self._notify.report(
                    f"status: equity ${g.equity:.2f} (peak ${g.peak:.2f}) | trades {st['trades']} "
                    f"WR {st['win_rate']:.0%} net ${st['net_pnl']:+.2f} | open {g.open_count} | "
                    f"feed age {age:.0f}s, reconnects {self._market.reconnects}/{self._public.reconnects}, "
                    f"max lag {self.max_loop_lag:.2f}s"
                    + (f" | HALT {g.halted_reason}" if g.halted_reason else "")
                )
            except Exception:  # noqa: BLE001 - status must never stop the engine
                logger.exception("status report failed")
