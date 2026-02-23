"""Binance Futures REST client - ccxt wrapper for USDT-M futures."""

from __future__ import annotations

import logging
from typing import Any

import ccxt.async_support as ccxt

from config.settings import BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET

logger = logging.getLogger(__name__)


class BinanceClient:
    """ccxt-based async Binance USDT-M futures client."""

    def __init__(
        self,
        api_key: str = BINANCE_API_KEY,
        api_secret: str = BINANCE_API_SECRET,
        testnet: bool = BINANCE_TESTNET,
    ) -> None:
        options: dict[str, Any] = {
            "defaultType": "future",
            "adjustForTimeDifference": True,
        }
        if testnet:
            options["sandboxMode"] = True

        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "options": options,
            "enableRateLimit": True,
        })
        if testnet:
            self.exchange.set_sandbox_mode(True)

        self.testnet = testnet

    async def close(self) -> None:
        await self.exchange.close()

    async def __aenter__(self) -> BinanceClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # -- Market Data -----------------------------------------------

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 200,
    ) -> list[list]:
        """Fetch OHLCV candles."""
        return await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    async def fetch_tickers(self) -> dict[str, Any]:
        """Fetch all futures tickers."""
        return await self.exchange.fetch_tickers()

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        """Fetch a single ticker."""
        return await self.exchange.fetch_ticker(symbol)

    async def fetch_orderbook(self, symbol: str, limit: int = 10) -> dict[str, Any]:
        """Fetch order book."""
        return await self.exchange.fetch_order_book(symbol, limit=limit)

    # -- Account Data ----------------------------------------------

    async def fetch_balance(self) -> dict[str, Any]:
        """Fetch futures account balance."""
        return await self.exchange.fetch_balance({"type": "future"})

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[dict]:
        """Fetch open positions."""
        positions = await self.exchange.fetch_positions(symbols)
        return [p for p in positions if float(p.get("contracts", 0)) > 0]

    async def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
        """Fetch current funding rate for a symbol."""
        return await self.exchange.fetch_funding_rate(symbol)

    async def get_usdt_balance(self) -> float:
        """Get available USDT balance."""
        balance = await self.fetch_balance()
        usdt = balance.get("USDT", {})
        return float(usdt.get("free", 0))

    # -- Trading ---------------------------------------------------

    async def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        """Set leverage for a symbol."""
        try:
            return await self.exchange.set_leverage(leverage, symbol)
        except Exception as e:
            logger.warning("set_leverage failed for %s: %s", symbol, e)
            return {}

    async def set_margin_mode(self, symbol: str, mode: str = "isolated") -> None:
        """Set margin mode (isolated/cross)."""
        try:
            await self.exchange.set_margin_mode(mode, symbol)
        except Exception as e:
            # Already set to this mode
            if "No need to change" not in str(e):
                logger.warning("set_margin_mode failed for %s: %s", symbol, e)

    async def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Place a market order.

        Args:
            symbol: e.g. "BTC/USDT:USDT"
            side: "buy" or "sell"
            amount: Quantity in base currency
            params: Extra params (reduceOnly, etc.)
        """
        return await self.exchange.create_order(
            symbol, "market", side, amount, params=params or {}
        )

    async def create_stop_loss(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
    ) -> dict[str, Any]:
        """Place a stop-loss market order."""
        return await self.exchange.create_order(
            symbol, "STOP_MARKET", side, amount,
            params={"stopPrice": stop_price, "reduceOnly": True},
        )

    async def create_take_profit(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
    ) -> dict[str, Any]:
        """Place a take-profit market order."""
        return await self.exchange.create_order(
            symbol, "TAKE_PROFIT_MARKET", side, amount,
            params={"stopPrice": stop_price, "reduceOnly": True},
        )

    async def cancel_all_orders(self, symbol: str) -> list[dict]:
        """Cancel all open orders for a symbol."""
        try:
            return await self.exchange.cancel_all_orders(symbol)
        except Exception as e:
            logger.warning("cancel_all_orders failed for %s: %s", symbol, e)
            return []

    async def close_position(
        self,
        symbol: str,
        side: str,
        amount: float,
    ) -> dict[str, Any]:
        """Close an existing position with a market order."""
        close_side = "sell" if side == "buy" else "buy"
        return await self.exchange.create_order(
            symbol, "market", close_side, amount,
            params={"reduceOnly": True},
        )

    # -- Market Info ------------------------------------------------

    async def get_futures_symbols(self) -> list[str]:
        """Get all active USDT-M futures symbols."""
        await self.exchange.load_markets()
        symbols = []
        for sym, market in self.exchange.markets.items():
            if (
                market.get("future")
                and market.get("active")
                and market.get("quote") == "USDT"
                and market.get("linear")
            ):
                symbols.append(sym)
        return symbols

    async def get_mark_price(self, symbol: str) -> float | None:
        """Get current mark price."""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return float(ticker.get("last", 0))
        except Exception:
            return None
