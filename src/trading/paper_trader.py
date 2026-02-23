"""Paper trader - simulated futures order execution."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.risk.leverage_calc import PositionParams
from src.db.models import (
    save_trade,
    update_trade_status,
    open_position,
    close_position,
    update_position_price,
)

logger = logging.getLogger(__name__)


class PaperTrader:
    """Simulated futures order execution that mirrors live trading interface."""

    async def place_order(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        params: PositionParams,
        signal_id: int | None = None,
    ) -> dict[str, Any]:
        """Place a simulated futures order.

        Args:
            symbol: Trading pair
            direction: LONG or SHORT
            entry_price: Current price
            params: Calculated position parameters
            signal_id: Associated signal ID

        Returns:
            Dict with trade details
        """
        if params.position_size <= 0:
            return {"success": False, "error": "Invalid position size"}

        order_id = f"paper-{uuid.uuid4().hex[:12]}"

        # Save trade record
        trade_id = await save_trade(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            size=params.position_size,
            cost=params.notional_value,
            leverage=params.leverage,
            margin=params.margin_required,
            order_id=order_id,
            status="filled",
            is_paper=True,
            signal_id=signal_id,
        )

        # Mark as filled
        await update_trade_status(
            trade_id=trade_id,
            status="filled",
            fill_price=entry_price,
            fill_size=params.position_size,
        )

        # Open position
        position_id = await open_position(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            size=params.position_size,
            cost=params.notional_value,
            leverage=params.leverage,
            margin=params.margin_required,
            liquidation_price=params.liquidation_price,
            sl_price=params.sl_price,
            tp_price=params.tp_price,
            trailing_stop_pct=0.02,
            trade_id=trade_id,
            is_paper=True,
        )

        logger.info(
            "Paper trade: %s %s %.6f @ %.4f (lev=%dx margin=$%.2f) [%s]",
            direction, symbol, params.position_size,
            entry_price, params.leverage, params.margin_required, order_id,
        )

        return {
            "success": True,
            "trade_id": trade_id,
            "position_id": position_id,
            "order_id": order_id,
            "symbol": symbol,
            "direction": direction,
            "price": entry_price,
            "size": params.position_size,
            "cost": params.notional_value,
            "leverage": params.leverage,
            "margin": params.margin_required,
            "sl_price": params.sl_price,
            "tp_price": params.tp_price,
            "is_paper": True,
        }

    async def close_order(
        self,
        position: dict[str, Any],
        current_price: float,
        exit_reason: str = "",
    ) -> dict[str, Any]:
        """Simulate closing a futures position.

        Args:
            position: Position dict from DB
            current_price: Exit price
            exit_reason: Why the position is being closed

        Returns:
            Dict with close details
        """
        entry_price = position["entry_price"]
        size = position["size"]
        direction = position["direction"]
        leverage = position.get("leverage", 1)

        # Calculate P&L
        if direction == "LONG":
            pnl = (current_price - entry_price) * size
        else:  # SHORT
            pnl = (entry_price - current_price) * size

        # Subtract funding paid
        funding_paid = position.get("funding_paid", 0)
        net_pnl = pnl - abs(funding_paid)

        order_id = f"paper-close-{uuid.uuid4().hex[:12]}"

        # Save exit trade
        await save_trade(
            symbol=position["symbol"],
            direction="SHORT" if direction == "LONG" else "LONG",
            entry_price=current_price,
            size=size,
            cost=current_price * size,
            leverage=leverage,
            margin=0,
            order_id=order_id,
            status="filled",
            is_paper=True,
        )

        # Close position
        await close_position(
            position["id"], realized_pnl=net_pnl, exit_reason=exit_reason
        )

        logger.info(
            "Paper close: %s %s @ %.4f | P&L: $%.4f (funding: $%.4f) [%s]",
            position["symbol"], direction, current_price,
            net_pnl, funding_paid, order_id,
        )

        return {
            "success": True,
            "order_id": order_id,
            "exit_price": current_price,
            "pnl": net_pnl,
            "exit_reason": exit_reason,
            "is_paper": True,
        }
