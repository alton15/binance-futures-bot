"""Supervised Binance USDT-M combined-stream WebSocket client.

Binance split futures WS hosts on 2026-04-23:
  /public  -> bookTicker / depth
  /market  -> aggTrade, markPrice, klines, tickers, forceOrder
Legacy /ws and /stream paths no longer deliver market streams.

Guarantees:
- never gives up reconnecting (capped exponential backoff)
- reconnects when the stream goes silent (stale_s) or nears the 24h limit
- desired subscriptions survive reconnects; SUBSCRIBE/UNSUBSCRIBE are live
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from typing import Callable

import websockets

logger = logging.getLogger(__name__)

MARKET_URL = "wss://fstream.binance.com/market/stream"
PUBLIC_URL = "wss://fstream.binance.com/public/stream"
MAX_BACKOFF_S = 30.0
MAX_STREAMS_PER_CONN = 1000
SUB_BATCH = 100
CLOSE_TIMEOUT_S = 2.0

MessageHandler = Callable[[str, dict], None]


class StreamClient:
    """One combined-stream connection with a supervisor loop.

    The handler is called synchronously for every message: keep it fast and
    spawn tasks for slow work.
    """

    def __init__(self, base_url: str, handler: MessageHandler, name: str,
                 stale_s: float = 15.0, max_age_s: float = 23 * 3600) -> None:
        self._base_url = base_url
        self._handler = handler
        self._name = name
        self._stale_s = stale_s
        self._max_age_s = max_age_s
        self._streams: set[str] = set()
        self._ws: websockets.ClientConnection | None = None
        self._ids = itertools.count(1)
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.last_message_at = 0.0
        self.connected = asyncio.Event()
        self.reconnects = 0

    @property
    def streams(self) -> frozenset[str]:
        return frozenset(self._streams)

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._supervise(), name=f"ws-{self._name}")
        return self._task

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise  # the caller itself is being cancelled; don't swallow it
            except Exception:  # noqa: BLE001 - shutting down, errors already logged
                logger.debug("[%s] supervisor ended with error during stop", self._name)

    async def set_streams(self, streams: set[str]) -> None:
        """Make the live subscription equal to `streams`."""
        if len(streams) > MAX_STREAMS_PER_CONN:
            raise ValueError(f"{len(streams)} streams exceeds per-connection limit")
        add = sorted(streams - self._streams)
        remove = sorted(self._streams - streams)
        self._streams = set(streams)
        if self._ws is None or not self.connected.is_set():
            return  # applied on (re)connect
        for method, items in (("UNSUBSCRIBE", remove), ("SUBSCRIBE", add)):
            for i in range(0, len(items), SUB_BATCH):
                await self._send(method, items[i:i + SUB_BATCH])

    async def _send(self, method: str, params: list[str]) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"method": method, "params": params, "id": next(self._ids)}))
            logger.info("[%s] %s %d streams", self._name, method, len(params))
        except websockets.ConnectionClosed:
            logger.warning("[%s] %s failed: connection closed (will resync on reconnect)", self._name, method)

    def _url(self) -> str:
        # Connect with at most one batch in the URL; the rest via SUBSCRIBE.
        first = sorted(self._streams)[:SUB_BATCH]
        return f"{self._base_url}?streams={'/'.join(first)}" if first else self._base_url

    async def _supervise(self) -> None:
        backoff = 1.0
        while not self._stopping:
            started = time.monotonic()
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any failure => reconnect
                logger.warning("[%s] connection error: %s: %s", self._name, type(e).__name__, e)
            finally:
                self.connected.clear()
                self._ws = None
            if self._stopping:
                break
            if time.monotonic() - started > 60:
                backoff = 1.0
            self.reconnects += 1
            logger.info("[%s] reconnecting in %.0fs (reconnects=%d)", self._name, backoff, self.reconnects)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)

    async def _connect_once(self) -> None:
        async with websockets.connect(self._url(), open_timeout=15, ping_interval=20,
                                      ping_timeout=20, close_timeout=CLOSE_TIMEOUT_S,
                                      max_queue=4096) as ws:
            self._ws = ws
            opened = time.monotonic()
            self.last_message_at = time.monotonic()
            in_url = set(sorted(self._streams)[:SUB_BATCH])
            rest = sorted(self._streams - in_url)
            self.connected.set()
            logger.info("[%s] connected (%d streams)", self._name, len(self._streams))
            for i in range(0, len(rest), SUB_BATCH):
                await self._send("SUBSCRIBE", rest[i:i + SUB_BATCH])
            while not self._stopping:
                if time.monotonic() - opened > self._max_age_s:
                    logger.info("[%s] max connection age reached, recycling", self._name)
                    return
                timeout = self._stale_s if self._streams else None
                try:
                    # asyncio.timeout, not wait_for: 3.11 wait_for can swallow a
                    # cancel when recv() completes at the same instant.
                    async with asyncio.timeout(timeout):
                        raw = await ws.recv()
                except TimeoutError:
                    logger.warning("[%s] no data for %.0fs, reconnecting", self._name, self._stale_s)
                    return
                self.last_message_at = time.monotonic()
                self._dispatch(raw)

    def _dispatch(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[%s] invalid JSON", self._name)
            return
        if "stream" not in msg:
            if msg.get("error"):
                logger.error("[%s] server error: %s", self._name, msg["error"])
            return  # subscription acks
        try:
            self._handler(msg["stream"], msg["data"])
        except Exception:  # noqa: BLE001 - a handler bug must not kill the stream
            logger.exception("[%s] handler failed for %s", self._name, msg.get("stream"))
