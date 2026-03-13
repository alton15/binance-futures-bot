"""ScalpWatcher - real-time spike detection via WebSocket miniTicker stream."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from config.settings import SCALP_SETTINGS, RISK, TRADING_MODE
from config.profiles import get_profile
from src.clients.binance_rest import BinanceClient
from src.clients.binance_ws import BinanceWSClient
from src.db.models import has_position_for_symbol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickerSnapshot:
    """Single ticker data point in the sliding window."""

    symbol: str
    price: float
    volume: float  # cumulative quote volume (USDT)
    timestamp: float


@dataclass(frozen=True)
class SpikeEvent:
    """Detected spike event to pass to ScalpPipeline."""

    symbol: str
    trigger_type: str  # "volume_spike" | "price_move" | "hot_coin"
    magnitude: float   # e.g. 3.5x volume or 0.021 (2.1% price change)
    price: float
    volume_24h: float
    detected_at: float


class ScalpWatcher:
    """Real-time coin spike detection engine.

    Subscribes to !miniTicker@arr WebSocket stream and detects:
    1. Volume spikes (recent 5m vol > 15m avg × 3)
    2. Price moves (±1.5% in 5 minutes)
    3. Hot coins (top gainers/losers/volume via REST polling)
    """

    def __init__(self, profile_name: str = "scalp") -> None:
        self._profile_name = profile_name
        self._ws_client = BinanceWSClient()

        # Settings
        self._volume_multiplier = SCALP_SETTINGS["volume_spike_multiplier"]
        self._price_threshold = SCALP_SETTINGS["price_move_threshold"]
        self._spike_window = SCALP_SETTINGS["spike_window_seconds"]
        self._history_window = SCALP_SETTINGS["history_window_seconds"]
        self._min_volume_24h = SCALP_SETTINGS["min_volume_24h"]

        # Sliding window: symbol -> deque[TickerSnapshot]
        self._ticker_window: dict[str, deque[TickerSnapshot]] = defaultdict(
            lambda: deque(maxlen=1000)
        )

        # Cooldown tracking: symbol -> last analysis timestamp
        self._cooldowns: dict[str, float] = {}
        profile = get_profile(profile_name)
        self._cooldown_seconds: int = profile.get_risk("analysis_cooldown_seconds")

        # Eligible symbols cache (refreshed periodically)
        self._eligible_symbols: set[str] = set()
        self._eligible_refresh_at: float = 0

        # Callback for spike events
        self._on_spike: Callable[[SpikeEvent], Coroutine] | None = None

        # 24h volume cache from miniTicker
        self._volume_24h: dict[str, float] = {}

    async def run(
        self,
        on_spike: Callable[[SpikeEvent], Coroutine],
    ) -> None:
        """Start watching for spikes via WebSocket.

        Args:
            on_spike: Async callback invoked when a spike is detected.
        """
        self._on_spike = on_spike
        logger.info("ScalpWatcher starting - subscribing to !miniTicker@arr")

        await self._refresh_eligible_symbols()
        await self._ws_client.subscribe_all_mini_tickers(self._on_mini_ticker)

        # Keep running until cancelled
        try:
            while True:
                await asyncio.sleep(60)
                # Refresh eligible symbols every 5 minutes
                if time.time() > self._eligible_refresh_at:
                    await self._refresh_eligible_symbols()
        except asyncio.CancelledError:
            logger.info("ScalpWatcher shutting down")
            await self._ws_client.close()

    async def poll_hot_coins(self) -> None:
        """Poll Binance for top gainers/losers/volume (REST API).

        Called periodically (every 3 minutes) by the scalp runner.
        """
        try:
            async with BinanceClient() as client:
                tickers = await client.fetch_tickers()
        except Exception as e:
            logger.error("Hot coin poll failed: %s", e)
            return

        if not tickers:
            return

        # Filter USDT perpetuals only
        usdt_tickers = [
            t for t in tickers.values()
            if isinstance(t, dict)
            and str(t.get("symbol", "")).endswith("USDT")
            and t.get("quoteVolume") is not None
        ]

        # Top gainers/losers by price change %
        sorted_by_change = sorted(
            usdt_tickers,
            key=lambda t: abs(float(t.get("percentage", 0) or 0)),
            reverse=True,
        )
        # Top volume
        sorted_by_volume = sorted(
            usdt_tickers,
            key=lambda t: float(t.get("quoteVolume", 0) or 0),
            reverse=True,
        )

        hot_symbols: set[str] = set()
        for t in sorted_by_change[:10]:
            hot_symbols.add(str(t["symbol"]))
        for t in sorted_by_volume[:10]:
            hot_symbols.add(str(t["symbol"]))

        now = time.time()
        emitted = 0
        for symbol in hot_symbols:
            if self._is_on_cooldown(symbol):
                continue
            if not self._is_eligible_symbol(symbol):
                continue

            vol_24h = float(
                next(
                    (t.get("quoteVolume", 0) for t in usdt_tickers if t.get("symbol") == symbol),
                    0,
                )
            )
            price = float(
                next(
                    (t.get("last", 0) or t.get("close", 0) for t in usdt_tickers if t.get("symbol") == symbol),
                    0,
                )
            )
            pct = float(
                next(
                    (t.get("percentage", 0) for t in usdt_tickers if t.get("symbol") == symbol),
                    0,
                )
            )

            event = SpikeEvent(
                symbol=symbol,
                trigger_type="hot_coin",
                magnitude=pct / 100 if pct else 0,
                price=price,
                volume_24h=vol_24h,
                detected_at=now,
            )
            await self._emit_spike(event)
            emitted += 1

        if emitted:
            logger.info("Hot coin poll: emitted %d spike events", emitted)

    async def _on_mini_ticker(self, data: list[dict] | dict) -> None:
        """Process !miniTicker@arr stream data (called ~1/sec)."""
        # miniTicker@arr sends a list; single stream sends a dict
        if isinstance(data, dict):
            data = [data]

        now = time.time()

        for ticker in data:
            symbol = ticker.get("s", "")
            if not symbol:
                continue

            # Quick filter: only USDT perpetuals
            if not symbol.endswith("USDT"):
                continue

            price = float(ticker.get("c", 0))
            quote_volume = float(ticker.get("q", 0))

            if price <= 0:
                continue

            # Cache 24h volume
            self._volume_24h[symbol] = quote_volume

            # Skip low-volume coins early
            if quote_volume < self._min_volume_24h:
                continue

            # Add to sliding window
            window = self._ticker_window[symbol]
            window.append(TickerSnapshot(symbol, price, quote_volume, now))

            # Prune old entries (> history_window)
            while window and (now - window[0].timestamp) > self._history_window:
                window.popleft()

            # Need enough data points
            if len(window) < 10:
                continue

            # Skip if on cooldown or ineligible
            if self._is_on_cooldown(symbol):
                continue

            # --- Volume spike check ---
            recent_vol = self._calc_volume_delta(window, self._spike_window)
            avg_vol = self._calc_volume_delta(window, self._history_window)

            # Normalize to per-second rate then compare
            if avg_vol > 0:
                # recent rate vs average rate
                recent_rate = recent_vol / self._spike_window
                avg_rate = avg_vol / self._history_window
                if avg_rate > 0 and recent_rate > avg_rate * self._volume_multiplier:
                    ratio = recent_rate / avg_rate
                    event = SpikeEvent(
                        symbol=symbol,
                        trigger_type="volume_spike",
                        magnitude=ratio,
                        price=price,
                        volume_24h=quote_volume,
                        detected_at=now,
                    )
                    await self._emit_spike(event)
                    continue  # Don't double-trigger

            # --- Price move check ---
            price_ago = self._get_price_at(window, self._spike_window)
            if price_ago and price_ago > 0:
                pct_change = (price - price_ago) / price_ago
                if abs(pct_change) >= self._price_threshold:
                    event = SpikeEvent(
                        symbol=symbol,
                        trigger_type="price_move",
                        magnitude=pct_change,
                        price=price,
                        volume_24h=quote_volume,
                        detected_at=now,
                    )
                    await self._emit_spike(event)

    async def _emit_spike(self, event: SpikeEvent) -> None:
        """Emit a spike event and set cooldown."""
        # Double-check eligibility (position check is async)
        is_paper = TRADING_MODE == "paper"
        has_pos = await has_position_for_symbol(
            event.symbol, is_paper=is_paper, profile=self._profile_name,
        )
        if has_pos:
            return

        self._cooldowns[event.symbol] = time.time()
        logger.info(
            "SPIKE [%s] %s: magnitude=%.4f price=%.4f vol24h=$%.0f",
            event.trigger_type, event.symbol,
            event.magnitude, event.price, event.volume_24h,
        )

        if self._on_spike:
            try:
                await self._on_spike(event)
            except Exception as e:
                logger.error("Spike callback error for %s: %s", event.symbol, e)

    def _is_on_cooldown(self, symbol: str) -> bool:
        """Check if symbol is within analysis cooldown period."""
        last = self._cooldowns.get(symbol, 0)
        return (time.time() - last) < self._cooldown_seconds

    def _is_eligible_symbol(self, symbol: str) -> bool:
        """Check if symbol passes basic eligibility filters."""
        if not symbol.endswith("USDT"):
            return False
        # Check against eligible set if populated
        if self._eligible_symbols and symbol not in self._eligible_symbols:
            return False
        return True

    async def _refresh_eligible_symbols(self) -> None:
        """Refresh the set of eligible USDT-M futures symbols."""
        try:
            async with BinanceClient() as client:
                symbols = await client.get_futures_symbols()
            self._eligible_symbols = {
                s.replace("/USDT:USDT", "USDT").replace("/", "")
                for s in symbols
            }
            logger.info("Refreshed eligible symbols: %d", len(self._eligible_symbols))
        except Exception as e:
            logger.warning("Failed to refresh eligible symbols: %s", e)
        self._eligible_refresh_at = time.time() + 300  # refresh every 5 min

    @staticmethod
    def _calc_volume_delta(
        window: deque[TickerSnapshot],
        seconds: float,
    ) -> float:
        """Calculate volume change within the given time window.

        Since miniTicker reports cumulative volume, we use the difference
        between the earliest and latest snapshots within the window.
        """
        if len(window) < 2:
            return 0

        now = window[-1].timestamp
        cutoff = now - seconds

        # Find earliest snapshot within the window
        earliest = None
        for snap in window:
            if snap.timestamp >= cutoff:
                earliest = snap
                break

        if earliest is None:
            return 0

        # Volume delta = latest cumulative - earliest cumulative
        delta = window[-1].volume - earliest.volume
        return max(0, delta)

    @staticmethod
    def _get_price_at(
        window: deque[TickerSnapshot],
        seconds_ago: float,
    ) -> float | None:
        """Get the price closest to `seconds_ago` from the window."""
        if not window:
            return None

        target_time = window[-1].timestamp - seconds_ago
        best: TickerSnapshot | None = None
        best_diff = float("inf")

        for snap in window:
            diff = abs(snap.timestamp - target_time)
            if diff < best_diff:
                best_diff = diff
                best = snap

        return best.price if best else None

    async def close(self) -> None:
        """Shut down the watcher."""
        await self._ws_client.close()
