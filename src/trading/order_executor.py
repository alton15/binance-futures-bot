"""Live order executor - real Binance futures order execution."""

from __future__ import annotations

import logging
from typing import Any

from src.clients.binance_rest import BinanceClient
from src.risk.leverage_calc import PositionParams
from src.db.models import (
    save_trade,
    update_trade_status,
    open_position,
    close_position,
    save_order,
)

logger = logging.getLogger(__name__)


class OrderExecutor:
    """Live futures order execution via Binance API."""

    def __init__(self, client: BinanceClient) -> None:
        self.client = client

    async def place_order(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        params: PositionParams,
        signal_id: int | None = None,
    ) -> dict[str, Any]:
        """Place a live futures order with SL/TP.

        Args:
            symbol: Trading pair (e.g. BTC/USDT:USDT)
            direction: LONG or SHORT
            entry_price: Expected entry price
            params: Calculated position parameters
            signal_id: Associated signal ID
        """
        if params.position_size <= 0:
            return {"success": False, "error": "Invalid position size"}

        try:
            # Set margin mode and leverage
            await self.client.set_margin_mode(symbol, "isolated")
            await self.client.set_leverage(symbol, params.leverage)

            # Place market order
            side = "buy" if direction == "LONG" else "sell"
            order = await self.client.create_market_order(
                symbol, side, params.position_size,
            )

            fill_price = float(order.get("average", entry_price) or entry_price)
            fill_size = float(order.get("filled", params.position_size) or params.position_size)
            order_id = order.get("id", "")

            # Save trade
            trade_id = await save_trade(
                symbol=symbol,
                direction=direction,
                entry_price=fill_price,
                size=fill_size,
                cost=fill_price * fill_size,
                leverage=params.leverage,
                margin=params.margin_required,
                order_id=str(order_id),
                status="filled",
                is_paper=False,
                signal_id=signal_id,
            )

            await update_trade_status(
                trade_id, "filled",
                fill_price=fill_price, fill_size=fill_size,
            )

            # Open position in DB
            position_id = await open_position(
                symbol=symbol,
                direction=direction,
                entry_price=fill_price,
                size=fill_size,
                cost=fill_price * fill_size,
                leverage=params.leverage,
                margin=params.margin_required,
                liquidation_price=params.liquidation_price,
                sl_price=params.sl_price,
                tp_price=params.tp_price,
                trailing_stop_pct=0.02,
                trade_id=trade_id,
                is_paper=False,
            )

            # Place SL order
            sl_side = "sell" if direction == "LONG" else "buy"
            try:
                sl_order = await self.client.create_stop_loss(
                    symbol, sl_side, fill_size, params.sl_price,
                )
                await save_order(
                    symbol=symbol,
                    position_id=position_id,
                    order_type="stop_loss",
                    side=sl_side,
                    size=fill_size,
                    price=params.sl_price,
                    exchange_order_id=str(sl_order.get("id", "")),
                    is_paper=False,
                )
            except Exception as e:
                logger.warning("Failed to place SL for %s: %s", symbol, e)

            # Place TP order
            try:
                tp_order = await self.client.create_take_profit(
                    symbol, sl_side, fill_size, params.tp_price,
                )
                await save_order(
                    symbol=symbol,
                    position_id=position_id,
                    order_type="take_profit",
                    side=sl_side,
                    size=fill_size,
                    price=params.tp_price,
                    exchange_order_id=str(tp_order.get("id", "")),
                    is_paper=False,
                )
            except Exception as e:
                logger.warning("Failed to place TP for %s: %s", symbol, e)

            logger.info(
                "Live trade: %s %s %.6f @ %.4f (lev=%dx) [%s]",
                direction, symbol, fill_size, fill_price,
                params.leverage, order_id,
            )

            return {
                "success": True,
                "trade_id": trade_id,
                "position_id": position_id,
                "order_id": str(order_id),
                "symbol": symbol,
                "direction": direction,
                "price": fill_price,
                "size": fill_size,
                "cost": fill_price * fill_size,
                "leverage": params.leverage,
                "margin": params.margin_required,
                "sl_price": params.sl_price,
                "tp_price": params.tp_price,
                "is_paper": False,
            }

        except Exception as e:
            logger.error("Order execution failed for %s: %s", symbol, e)
            return {"success": False, "error": str(e)}

    async def close_order(
        self,
        position: dict[str, Any],
        current_price: float,
        exit_reason: str = "",
    ) -> dict[str, Any]:
        """Close a live futures position."""
        symbol = position["symbol"]
        direction = position["direction"]
        size = position["size"]

        try:
            # Cancel existing SL/TP orders
            await self.client.cancel_all_orders(symbol)

            # Close with market order
            side = "buy" if direction == "LONG" else "sell"
            order = await self.client.close_position(symbol, side, size)

            fill_price = float(order.get("average", current_price) or current_price)

            # Calculate P&L
            if direction == "LONG":
                pnl = (fill_price - position["entry_price"]) * size
            else:
                pnl = (position["entry_price"] - fill_price) * size

            funding_paid = position.get("funding_paid", 0)
            net_pnl = pnl - abs(funding_paid)

            await close_position(
                position["id"], realized_pnl=net_pnl, exit_reason=exit_reason
            )

            logger.info(
                "Live close: %s %s @ %.4f | P&L: $%.4f [%s]",
                symbol, direction, fill_price, net_pnl,
                order.get("id", ""),
            )

            return {
                "success": True,
                "exit_price": fill_price,
                "pnl": net_pnl,
                "exit_reason": exit_reason,
                "is_paper": False,
            }

        except Exception as e:
            logger.error("Close failed for %s: %s", symbol, e)
            return {"success": False, "error": str(e)}
