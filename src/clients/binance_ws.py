"""Binance Futures WebSocket client - real-time price and user data streams."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

import websockets

from config.settings import BINANCE_TESTNET, WS

logger = logging.getLogger(__name__)

# Binance Futures WebSocket endpoints
WS_BASE = "wss://fstream.binance.com/ws"
WS_TESTNET_BASE = "wss://stream.binancefuture.com/ws"


class BinanceWSClient:
    """WebSocket client for Binance USDT-M futures streams.

    Streams:
    - Mark price: real-time mark price for liquidation monitoring
    - Book ticker: real-time best bid/ask for spread monitoring
    - User data: account updates, order fills, position changes (live mode)
    """

    def __init__(self, testnet: bool = BINANCE_TESTNET) -> None:
        self.base_url = WS_TESTNET_BASE if testnet else WS_BASE
        self._connections: dict[str, Any] = {}
        self._running = False
        self._callbacks: dict[str, Callable] = {}
        self._reconnect_attempts: dict[str, int] = {}

    async def subscribe_mark_price(
        self,
        symbols: list[str],
        callback: Callable[[dict], Coroutine],
    ) -> None:
        """Subscribe to mark price updates for multiple symbols.

        Callback receives: {"symbol": "BTCUSDT", "markPrice": "50000.00", ...}
        """
        streams = [f"{s.lower().replace('/', '').replace(':usdt', '')}@markPrice@1s" for s in symbols]
        self._callbacks["markPrice"] = callback
        await self._connect_combined(streams, "markPrice")

    async def subscribe_book_ticker(
        self,
        symbols: list[str],
        callback: Callable[[dict], Coroutine],
    ) -> None:
        """Subscribe to book ticker (best bid/ask) for symbols.

        Callback receives: {"symbol": "BTCUSDT", "bestBid": "...", "bestAsk": "...", ...}
        """
        streams = [f"{s.lower().replace('/', '').replace(':usdt', '')}@bookTicker" for s in symbols]
        self._callbacks["bookTicker"] = callback
        await self._connect_combined(streams, "bookTicker")

    async def _connect_combined(self, streams: list[str], stream_id: str) -> None:
        """Connect to combined stream endpoint."""
        stream_path = "/".join(streams)
        url = f"{self.base_url}/{stream_path}"
        self._running = True
        self._reconnect_attempts[stream_id] = 0

        asyncio.create_task(self._listen(url, stream_id))

    async def _listen(self, url: str, stream_id: str) -> None:
        """Listen to WebSocket stream with auto-reconnect."""
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=WS["heartbeat_interval"]) as ws:
                    self._connections[stream_id] = ws
                    self._reconnect_attempts[stream_id] = 0
                    logger.info("WebSocket connected: %s", stream_id)

                    async for message in ws:
                        try:
                            data = json.loads(message)
                            # Combined stream wraps data in {"stream": ..., "data": ...}
                            if "data" in data:
                                data = data["data"]

                            callback = self._callbacks.get(stream_id)
                            if callback:
                                await callback(data)
                        except json.JSONDecodeError:
                            logger.warning("Invalid JSON from %s", stream_id)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning("WebSocket %s closed: %s", stream_id, e)
            except Exception as e:
                logger.error("WebSocket %s error: %s", stream_id, e)

            # Reconnect logic
            if not self._running:
                break

            attempts = self._reconnect_attempts.get(stream_id, 0)
            if attempts >= WS["max_reconnect_attempts"]:
                logger.error("Max reconnect attempts reached for %s", stream_id)
                break

            self._reconnect_attempts[stream_id] = attempts + 1
            delay = WS["reconnect_delay"] * (attempts + 1)
            logger.info("Reconnecting %s in %ds (attempt %d)...", stream_id, delay, attempts + 1)
            await asyncio.sleep(delay)

    async def close(self) -> None:
        """Close all WebSocket connections."""
        self._running = False
        for stream_id, ws in self._connections.items():
            try:
                await ws.close()
                logger.info("WebSocket closed: %s", stream_id)
            except Exception:
                pass
        self._connections.clear()

    async def __aenter__(self) -> BinanceWSClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
