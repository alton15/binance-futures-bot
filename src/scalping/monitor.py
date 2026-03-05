"""ScalpMonitor - WebSocket-based real-time position monitoring for scalp trades."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from config.settings import TRADING_MODE
from src.clients.binance_ws import BinanceWSClient
from src.clients.binance_rest import BinanceClient
from src.db.models import (
    get_open_positions,
    update_position_price,
)
from src.trading.position_monitor import _should_exit, _execute_exit
from src.notifications.notifier import notify_exit

if TYPE_CHECKING:
    from config.profiles import ProfileConfig

logger = logging.getLogger(__name__)


class ScalpMonitor:
    """Real-time position monitor using markPrice@1s WebSocket stream.

    Unlike the regular position_monitor (5-min REST polling), this monitors
    scalp positions every second via WebSocket for immediate SL/TP execution.
    """

    def __init__(self, profile: ProfileConfig | None = None) -> None:
        from config.profiles import get_profile
        self._profile = profile or get_profile("scalp")
        self._profile_name = self._profile.name
        self._ws_client = BinanceWSClient()

        # Currently monitored symbols
        self._monitored_symbols: set[str] = set()

        # Position cache: symbol -> list[position_dict]
        self._positions: dict[str, list[dict[str, Any]]] = {}

        # Trailing high/low tracking: position_id -> (trailing_high, trailing_low)
        self._trailing: dict[int, tuple[float, float]] = {}

        # Lock for position updates
        self._lock = asyncio.Lock()

    async def run(self) -> None:
        """Start monitoring scalp positions via WebSocket.

        Periodically refreshes the position list and subscribes to
        markPrice streams for active symbols.
        """
        logger.info("ScalpMonitor starting (profile=%s)", self._profile_name)

        try:
            while True:
                await self._refresh_positions()
                await asyncio.sleep(10)  # Refresh position list every 10 seconds
        except asyncio.CancelledError:
            logger.info("ScalpMonitor shutting down")
            await self._ws_client.close()

    async def _refresh_positions(self) -> None:
        """Refresh open positions and update WebSocket subscriptions."""
        is_paper = TRADING_MODE == "paper"
        positions = await get_open_positions(
            is_paper=is_paper, profile=self._profile_name,
        )

        async with self._lock:
            # Rebuild position cache
            self._positions.clear()
            for pos in positions:
                symbol = pos["symbol"]
                self._positions.setdefault(symbol, []).append(pos)

                # Initialize trailing tracking
                pos_id = pos["id"]
                if pos_id not in self._trailing:
                    entry = pos["entry_price"]
                    self._trailing[pos_id] = (
                        pos.get("trailing_high") or entry,
                        pos.get("trailing_low") or entry,
                    )

            # Clean up trailing for closed positions
            active_ids = {pos["id"] for pos in positions}
            stale_ids = [pid for pid in self._trailing if pid not in active_ids]
            for pid in stale_ids:
                del self._trailing[pid]

        # Determine which symbols need monitoring
        current_symbols = set(self._positions.keys())
        new_symbols = current_symbols - self._monitored_symbols
        removed_symbols = self._monitored_symbols - current_symbols

        if new_symbols:
            # Subscribe to markPrice for new symbols
            # Convert ccxt format to WS format
            ws_symbols = list(new_symbols)
            logger.info(
                "ScalpMonitor: subscribing to markPrice for %d symbols: %s",
                len(ws_symbols), ws_symbols,
            )
            await self._ws_client.subscribe_mark_price(
                ws_symbols, self._on_mark_price,
            )
            self._monitored_symbols.update(new_symbols)

        if removed_symbols:
            logger.info(
                "ScalpMonitor: %d symbols no longer have positions: %s",
                len(removed_symbols), removed_symbols,
            )
            # Note: WebSocket subscriptions are not individually cancellable
            # with the current client. They'll be ignored in the callback.
            self._monitored_symbols -= removed_symbols

        if not current_symbols and self._monitored_symbols:
            # No positions left, clean up
            self._monitored_symbols.clear()

    async def _on_mark_price(self, data: dict) -> None:
        """Handle markPrice@1s tick for a symbol.

        Checks all open positions for exit conditions on every tick.
        """
        symbol = data.get("s", "")
        mark_price_str = data.get("p", "0")

        try:
            mark_price = float(mark_price_str)
        except (ValueError, TypeError):
            return

        if mark_price <= 0:
            return

        async with self._lock:
            positions = self._positions.get(symbol, [])

        if not positions:
            return

        is_paper = TRADING_MODE == "paper"

        for pos in positions:
            try:
                await self._check_position_tick(pos, mark_price, is_paper)
            except Exception as e:
                logger.error(
                    "ScalpMonitor error for position %d (%s): %s",
                    pos.get("id", 0), symbol, e,
                )

    async def _check_position_tick(
        self,
        position: dict[str, Any],
        current_price: float,
        is_paper: bool,
    ) -> None:
        """Check a single position against the latest mark price."""
        pos_id = position["id"]
        direction = position["direction"]
        entry_price = position["entry_price"]
        size = position["size"]

        # Calculate unrealized PnL
        if direction == "LONG":
            unrealized_pnl = (current_price - entry_price) * size
        else:
            unrealized_pnl = (entry_price - current_price) * size

        # Update trailing high/low
        trailing_high, trailing_low = self._trailing.get(
            pos_id, (entry_price, entry_price),
        )

        if direction == "LONG":
            trailing_high = max(trailing_high, current_price)
        else:
            if trailing_low > 0:
                trailing_low = min(trailing_low, current_price)
            else:
                trailing_low = current_price

        self._trailing[pos_id] = (trailing_high, trailing_low)

        # Update position price in DB (throttled - only when significant change)
        # We don't want to write to DB every second for every position
        last_price = position.get("current_price", 0) or 0
        if last_price == 0 or abs(current_price - last_price) / max(last_price, 1e-8) > 0.0005:
            await update_position_price(
                pos_id,
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
                mark_price=current_price,
                trailing_high=trailing_high,
                trailing_low=trailing_low,
            )

        # Check exit conditions using the shared _should_exit logic
        exit_reason = _should_exit(
            position, current_price, trailing_high, trailing_low,
            funding_rate=0,  # Funding checked separately in regular intervals
            profile=self._profile,
        )

        if exit_reason:
            logger.info(
                "SCALP EXIT [%s] %s %s: %s (entry=%.4f curr=%.4f pnl=$%.4f)",
                self._profile_name, position["symbol"], direction,
                exit_reason, entry_price, current_price, unrealized_pnl,
            )

            # Execute exit immediately
            async with BinanceClient() as client:
                await _execute_exit(
                    client, position, current_price,
                    unrealized_pnl, exit_reason, is_paper,
                )

            # Remove from active tracking
            async with self._lock:
                symbol = position["symbol"]
                if symbol in self._positions:
                    self._positions[symbol] = [
                        p for p in self._positions[symbol]
                        if p["id"] != pos_id
                    ]
                    if not self._positions[symbol]:
                        del self._positions[symbol]
                if pos_id in self._trailing:
                    del self._trailing[pos_id]

    async def close(self) -> None:
        """Shut down the monitor."""
        await self._ws_client.close()
