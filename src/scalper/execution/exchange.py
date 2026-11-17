"""Thin, long-lived ccxt wrapper for Binance USDT-M (one instance per process).

ccxt >= 4.5.92 routes STOP_MARKET/TAKE_PROFIT_MARKET to the Algo Order API
(mandatory since 2025-12-09) automatically.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import ccxt.async_support as ccxt

from src.scalper.config import Settings

logger = logging.getLogger(__name__)

RETRYABLE = (ccxt.RequestTimeout, ccxt.ExchangeNotAvailable, ccxt.NetworkError, ccxt.DDoSProtection)
MAX_RETRIES = 3


@dataclass(frozen=True)
class MarketSpec:
    symbol: str          # ccxt symbol, e.g. BTC/USDT:USDT
    ws_id: str           # lowercase exchange id, e.g. btcusdt
    qty_step: float
    tick_size: float
    min_notional: float


def to_ws_id(market: dict[str, Any]) -> str:
    return str(market["id"]).lower()


class Exchange:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        use_keys = not settings.is_paper  # paper only needs public market data
        self.ccxt = ccxt.binanceusdm({
            "apiKey": (settings.api_key or None) if use_keys else None,
            "secret": (settings.api_secret or None) if use_keys else None,
            "enableRateLimit": True,
            "timeout": 15000,
            "options": {"defaultType": "future", "adjustForTimeDifference": True},
        })
        self.ccxt.has["fetchCurrencies"] = False
        if settings.testnet:
            self.ccxt.enable_demo_trading(True)
        self._specs: dict[str, MarketSpec] = {}
        self._by_ws: dict[str, str] = {}

    async def close(self) -> None:
        await self.ccxt.close()

    async def _call(self, fn, *args, **kwargs):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await fn(*args, **kwargs)
            except RETRYABLE as e:
                if attempt == MAX_RETRIES:
                    raise
                delay = 0.5 * 2 ** attempt
                logger.warning("retry %d/%d %s after %.1fs: %s", attempt, MAX_RETRIES,
                               getattr(fn, "__name__", fn), delay, e)
                await asyncio.sleep(delay)

    async def load_markets(self) -> dict[str, MarketSpec]:
        markets = await self._call(self.ccxt.load_markets, True)
        specs = {}
        for sym, m in markets.items():
            if not (m.get("swap") and m.get("linear") and m.get("quote") == "USDT" and m.get("active")):
                continue
            prec = m.get("precision") or {}
            limits = m.get("limits") or {}
            specs[sym] = MarketSpec(
                symbol=sym,
                ws_id=to_ws_id(m),
                qty_step=float(prec.get("amount") or 0.001),
                tick_size=float(prec.get("price") or 0.0001),
                min_notional=float((limits.get("cost") or {}).get("min") or 5.0),
            )
        self._specs = specs
        self._by_ws = {s.ws_id: s.symbol for s in specs.values()}
        logger.info("loaded %d USDT-M perpetual markets", len(specs))
        return specs

    def spec(self, symbol: str) -> MarketSpec:
        return self._specs[symbol]

    def has_spec(self, symbol: str) -> bool:
        return symbol in self._specs

    def symbol_for_ws(self, ws_id: str) -> str | None:
        return self._by_ws.get(ws_id.lower())

    async def top_symbols(self, min_quote_volume: float, limit: int, exclude: tuple[str, ...]) -> list[str]:
        """Most liquid USDT-M perps by 24h quote volume."""
        tickers = await self._call(self.ccxt.fetch_tickers)
        rows = []
        for sym, t in tickers.items():
            spec = self._specs.get(sym)
            if spec is None or spec.ws_id.upper() in exclude:
                continue
            qv = float(t.get("quoteVolume") or 0)
            if qv >= min_quote_volume:
                rows.append((qv, sym))
        rows.sort(reverse=True)
        return [s for _, s in rows[:limit]]

    # -- trading (live only) -------------------------------------------------
    async def setup_symbol(self, symbol: str, leverage: int) -> None:
        try:
            await self._call(self.ccxt.set_margin_mode, "isolated", symbol)
        except ccxt.ExchangeError as e:
            if "No need to change" not in str(e):
                raise
        await self._call(self.ccxt.set_leverage, leverage, symbol)

    async def post_only_limit(self, symbol: str, side: str, qty: float, price: float,
                              client_id: str) -> dict:
        return await self.ccxt.create_order(
            symbol, "limit", side, qty, price,
            {"timeInForce": "GTX", "newClientOrderId": client_id},
        )

    async def market(self, symbol: str, side: str, qty: float, reduce_only: bool, client_id: str) -> dict:
        params = {"newClientOrderId": client_id}
        if reduce_only:
            params["reduceOnly"] = True
        return await self.ccxt.create_order(symbol, "market", side, qty, None, params)

    async def reduce_only_limit(self, symbol: str, side: str, qty: float, price: float,
                                client_id: str) -> dict:
        return await self.ccxt.create_order(
            symbol, "limit", side, qty, price,
            {"timeInForce": "GTC", "reduceOnly": True, "newClientOrderId": client_id},
        )

    async def stop_market(self, symbol: str, side: str, qty: float, stop_price: float,
                          client_id: str) -> dict:
        return await self.ccxt.create_order(
            symbol, "STOP_MARKET", side, qty, None,
            {"stopPrice": stop_price, "reduceOnly": True, "workingType": "CONTRACT_PRICE",
             "clientAlgoId": client_id},
        )

    async def fetch_order(self, order_id: str, symbol: str, is_algo: bool = False) -> dict:
        params = {"trigger": True} if is_algo else {}
        return await self._call(self.ccxt.fetch_order, order_id, symbol, params)

    async def cancel(self, order_id: str, symbol: str, is_algo: bool = False) -> None:
        params = {"trigger": True} if is_algo else {}
        try:
            await self._call(self.ccxt.cancel_order, order_id, symbol, params)
        except ccxt.OrderNotFound:
            logger.info("cancel %s: already gone", order_id)

    async def position_qty(self, symbol: str) -> float:
        positions = await self._call(self.ccxt.fetch_positions, [symbol])
        total = 0.0
        for p in positions:
            contracts = float(p.get("contracts") or 0)
            total += contracts if p.get("side") == "long" else -contracts
        return total

    async def balance_usdt(self) -> float:
        bal = await self._call(self.ccxt.fetch_balance)
        return float((bal.get("total") or {}).get("USDT") or 0.0)

    def round_price(self, symbol: str, price: float) -> float:
        return float(self.ccxt.price_to_precision(symbol, price))

    def round_qty(self, symbol: str, qty: float) -> float:
        return float(self.ccxt.amount_to_precision(symbol, qty))
