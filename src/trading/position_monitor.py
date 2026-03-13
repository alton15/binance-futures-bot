"""Position monitor - tracks open positions and triggers exits."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from config.settings import RISK, TRADING_MODE
from src.clients.binance_rest import BinanceClient
from src.db.models import (
    get_open_positions,
    update_position_price,
    update_position_funding,
    save_funding_payment,
)
from src.notifications.notifier import notify_exit

if TYPE_CHECKING:
    from config.profiles import ProfileConfig

logger = logging.getLogger(__name__)


async def monitor_positions(
    client: BinanceClient,
    profile: ProfileConfig | None = None,
) -> None:
    """Check all open positions and trigger exits as needed.

    Exit conditions:
    1. Stop loss hit
    2. Take profit hit
    3. Trailing stop triggered
    4. Signal reversal (not implemented - requires re-analysis)
    5. Near liquidation
    6. Excessive funding rate
    7. Max hold time exceeded
    """
    is_paper = TRADING_MODE == "paper"
    profile_name = profile.name if profile else "neutral"
    positions = await get_open_positions(is_paper=is_paper, profile=profile_name)

    if not positions:
        logger.debug("No open positions to monitor (profile=%s)", profile_name)
        return

    logger.info("Monitoring %d open positions (profile=%s)...", len(positions), profile_name)

    for pos in positions:
        try:
            await _check_position(client, pos, is_paper, profile)
        except Exception as e:
            logger.error("Error monitoring position %d: %s", pos["id"], e)


async def _check_position(
    client: BinanceClient,
    position: dict[str, Any],
    is_paper: bool,
    profile: ProfileConfig | None = None,
) -> None:
    """Check a single position for exit conditions."""
    symbol = position["symbol"]

    # Get current price
    current_price = await client.get_mark_price(symbol)
    if current_price is None:
        logger.warning("Could not get price for %s", symbol)
        return

    entry_price = position["entry_price"]
    size = position["size"]
    direction = position["direction"]

    # Calculate unrealized PnL
    if direction == "LONG":
        unrealized_pnl = (current_price - entry_price) * size
    else:
        unrealized_pnl = (entry_price - current_price) * size

    # Update trailing high/low
    trailing_high = position.get("trailing_high") or entry_price
    trailing_low = position.get("trailing_low") or entry_price

    if direction == "LONG":
        trailing_high = max(trailing_high, current_price)
    else:
        trailing_low = min(trailing_low, current_price) if trailing_low > 0 else current_price

    # Update position
    await update_position_price(
        position["id"],
        current_price=current_price,
        unrealized_pnl=unrealized_pnl,
        mark_price=current_price,
        trailing_high=trailing_high,
        trailing_low=trailing_low,
    )

    # Check funding rate
    try:
        fr = await client.fetch_funding_rate(symbol)
        funding_rate = float(fr.get("fundingRate", 0) or 0)
        if funding_rate != 0:
            # Estimate funding payment
            notional = current_price * size
            if direction == "LONG":
                payment = notional * funding_rate
            else:
                payment = -notional * funding_rate
            # Only save if significant
            if abs(payment) > 0.001:
                await save_funding_payment(
                    symbol=symbol,
                    position_id=position["id"],
                    funding_rate=funding_rate,
                    payment=payment,
                )
                await update_position_funding(position["id"], payment)
    except Exception:
        funding_rate = 0

    # Check exit conditions
    exit_reason = _should_exit(position, current_price, trailing_high, trailing_low, funding_rate, profile)

    if exit_reason:
        logger.info(
            "EXIT triggered for %s %s: %s (entry=%.4f curr=%.4f pnl=$%.4f)",
            symbol, direction, exit_reason, entry_price, current_price, unrealized_pnl,
        )
        await _execute_exit(client, position, current_price, unrealized_pnl, exit_reason, is_paper)
    else:
        logger.debug(
            "HOLD %s %s: entry=%.4f curr=%.4f pnl=$%.4f",
            symbol, direction, entry_price, current_price, unrealized_pnl,
        )


def _should_exit(
    position: dict[str, Any],
    current_price: float,
    trailing_high: float,
    trailing_low: float,
    funding_rate: float,
    profile: ProfileConfig | None = None,
) -> str | None:
    """Determine if position should be exited using profile-specific thresholds."""
    direction = position["direction"]
    entry_price = position["entry_price"]
    sl_price = position.get("sl_price", 0)
    tp_price = position.get("tp_price", 0)
    liq_price = position.get("liquidation_price", 0)

    trailing_pct = position.get("trailing_stop_pct")
    if trailing_pct is None:
        trailing_pct = profile.get_risk("trailing_stop_pct") if profile else RISK["trailing_stop_pct"]

    funding_max = profile.get_risk("funding_rate_max") if profile else RISK["funding_rate_max"]
    max_hold = profile.get_risk("max_hold_hours") if profile else RISK["max_hold_hours"]

    # 1. Stop loss
    if sl_price and sl_price > 0:
        if direction == "LONG" and current_price <= sl_price:
            return f"stop_loss (price {current_price:.4f} <= SL {sl_price:.4f})"
        elif direction == "SHORT" and current_price >= sl_price:
            return f"stop_loss (price {current_price:.4f} >= SL {sl_price:.4f})"

    # 2. Take profit
    if tp_price and tp_price > 0:
        if direction == "LONG" and current_price >= tp_price:
            return f"take_profit (price {current_price:.4f} >= TP {tp_price:.4f})"
        elif direction == "SHORT" and current_price <= tp_price:
            return f"take_profit (price {current_price:.4f} <= TP {tp_price:.4f})"

    # 3. Trailing stop (ATR-based activation + dynamic distance)
    if trailing_pct and trailing_pct > 0:
        atr = position.get("atr", 0) or 0
        activation_atr = profile.get_risk("trailing_activation_atr") if profile else RISK.get("trailing_activation_atr", 1.0)
        trail_atr_mult = profile.get_risk("trailing_atr_multiplier") if profile else RISK.get("trailing_atr_multiplier", 1.5)

        # Minimum profit required before trailing activates (ATR-based)
        min_profit_distance = atr * activation_atr if atr > 0 else entry_price * trailing_pct

        # Dynamic trailing distance: max(fixed %, ATR-based)
        atr_trail_distance = atr * trail_atr_mult if atr > 0 else 0

        if direction == "LONG" and trailing_high > 0:
            profit_from_entry = trailing_high - entry_price
            if profit_from_entry >= min_profit_distance:
                # Use the larger of fixed % or ATR-based trailing distance
                fixed_trigger = trailing_high * (1 - trailing_pct)
                atr_trigger = trailing_high - atr_trail_distance if atr_trail_distance > 0 else fixed_trigger
                trail_trigger = min(fixed_trigger, atr_trigger)  # more conservative (wider)
                if current_price <= trail_trigger and current_price > entry_price:
                    return f"trailing_stop (high={trailing_high:.4f} trigger={trail_trigger:.4f})"
        elif direction == "SHORT" and trailing_low > 0:
            profit_from_entry = entry_price - trailing_low
            if profit_from_entry >= min_profit_distance:
                fixed_trigger = trailing_low * (1 + trailing_pct)
                atr_trigger = trailing_low + atr_trail_distance if atr_trail_distance > 0 else fixed_trigger
                trail_trigger = max(fixed_trigger, atr_trigger)  # more conservative (wider)
                if current_price >= trail_trigger and current_price < entry_price:
                    return f"trailing_stop (low={trailing_low:.4f} trigger={trail_trigger:.4f})"

    # 4. Near liquidation (within 5% of liquidation price)
    if liq_price and liq_price > 0:
        if direction == "LONG":
            liq_distance = (current_price - liq_price) / current_price
        else:
            liq_distance = (liq_price - current_price) / current_price

        if liq_distance < 0.05:
            return f"near_liquidation (distance={liq_distance:.1%})"

    # 5. Excessive funding rate
    if abs(funding_rate) > funding_max * 2:
        # Only exit if funding is against our position
        if (direction == "LONG" and funding_rate > 0) or \
           (direction == "SHORT" and funding_rate < 0):
            return f"excessive_funding (rate={funding_rate:.4%})"

    # 6. Max hold time
    opened_at = position.get("opened_at", "")
    if opened_at:
        try:
            opened_dt = datetime.fromisoformat(opened_at)
            if opened_dt.tzinfo is None:
                opened_dt = opened_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            hours_held = (now - opened_dt).total_seconds() / 3600
            if hours_held >= max_hold:
                return f"max_hold_time ({hours_held:.1f}h >= {max_hold}h)"
        except (ValueError, TypeError):
            pass

    return None


async def _execute_exit(
    client: BinanceClient,
    position: dict[str, Any],
    current_price: float,
    pnl: float,
    exit_reason: str,
    is_paper: bool,
) -> None:
    """Execute position exit."""
    profile_name = position.get("profile", "neutral")
    if is_paper:
        from src.trading.paper_trader import PaperTrader
        trader = PaperTrader(profile_name=profile_name)
    else:
        from src.trading.order_executor import OrderExecutor
        trader = OrderExecutor(client, profile_name=profile_name)

    result = await trader.close_order(position, current_price, exit_reason)

    if result.get("success"):
        actual_pnl = result.get("pnl", pnl)
        await notify_exit(position, actual_pnl, exit_reason)
        logger.info(
            "Position %d closed [%s]: %s | P&L: $%.4f",
            position["id"], profile_name, exit_reason, actual_pnl,
        )
    else:
        logger.error(
            "Failed to close position %d: %s",
            position["id"], result.get("error"),
        )
