"""Notifications - Discord webhook alerts with two channels."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from config.settings import DISCORD_WEBHOOK_ALERTS, DISCORD_WEBHOOK_REPORTS, INITIAL_CAPITAL, RISK

logger = logging.getLogger(__name__)


async def _send_discord(webhook_url: str, embeds: list[dict]) -> bool:
    """Send Discord embeds to a specific webhook URL."""
    if not webhook_url:
        logger.debug("No webhook URL configured, skipping notification")
        return False

    payload = {"embeds": embeds}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.warning("Failed to send notification: %s", e)
        return False


def _fmt_pnl(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}${val:.4f}"


def _progress_bar(ratio: float, length: int = 10) -> str:
    filled = int(ratio * length)
    empty = length - filled
    return "\u2588" * filled + "\u2591" * empty


# -- Alerts Channel ------------------------------------------------


async def notify_trade(trade: dict[str, Any]) -> None:
    """Send trade execution notification."""
    direction = trade.get("direction", "").upper()
    symbol = trade.get("symbol", "")
    cost = trade.get("cost", 0)
    price = trade.get("price", 0)
    leverage = trade.get("leverage", 1)
    is_paper = trade.get("is_paper", True)
    mode = "PAPER" if is_paper else "LIVE"

    embed = {
        "title": f"Trade Executed [{mode}]",
        "color": 0x00FF00 if direction == "LONG" else 0xFF6600,
        "fields": [
            {"name": "Symbol", "value": symbol, "inline": True},
            {"name": "Direction", "value": direction, "inline": True},
            {"name": "Leverage", "value": f"{leverage}x", "inline": True},
            {"name": "Entry", "value": f"${price:.4f}", "inline": True},
            {"name": "Size", "value": f"${cost:.2f}", "inline": True},
            {"name": "SL", "value": f"${trade.get('sl_price', 0):.4f}", "inline": True},
            {"name": "TP", "value": f"${trade.get('tp_price', 0):.4f}", "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await _send_discord(DISCORD_WEBHOOK_ALERTS, [embed])


async def notify_exit(
    position: dict[str, Any],
    pnl: float,
    exit_reason: str = "",
) -> None:
    """Send position exit notification."""
    color = 0x00FF00 if pnl >= 0 else 0xFF0000
    direction = position.get("direction", "")
    symbol = position.get("symbol", "")
    leverage = position.get("leverage", 1)

    embed = {
        "title": f"Position Closed [{'WIN' if pnl >= 0 else 'LOSS'}]",
        "color": color,
        "fields": [
            {"name": "Symbol", "value": symbol, "inline": True},
            {"name": "Direction", "value": direction, "inline": True},
            {"name": "Leverage", "value": f"{leverage}x", "inline": True},
            {"name": "P&L", "value": _fmt_pnl(pnl), "inline": True},
            {"name": "Entry", "value": f"${position.get('entry_price', 0):.4f}", "inline": True},
            {"name": "Reason", "value": exit_reason or "manual", "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await _send_discord(DISCORD_WEBHOOK_ALERTS, [embed])


# -- Reports Channel -----------------------------------------------


async def notify_status(
    stats: dict[str, Any],
    positions: list[dict],
    is_paper: bool = True,
) -> None:
    """Send compact status update to reports channel."""
    mode = "PAPER" if is_paper else "LIVE"
    pnl = stats.get("total_realized_pnl", 0)
    unrealized = stats.get("unrealized_pnl", 0)

    if positions:
        pos_lines = []
        for p in positions:
            entry = p.get("entry_price", 0)
            curr = p.get("current_price", entry)
            upnl = p.get("unrealized_pnl", 0)
            d = p.get("direction", "?")
            sym = p.get("symbol", "")[:20]
            lev = p.get("leverage", 1)
            pos_lines.append(f"`{d}` {sym} {lev}x | {entry:.2f} > {curr:.2f} | {_fmt_pnl(upnl)}")
        pos_text = "\n".join(pos_lines)
    else:
        pos_text = "None"

    embed = {
        "title": f"Status [{mode}]",
        "color": 0x2F3136,
        "fields": [
            {"name": "Trades", "value": str(stats.get("total_trades", 0)), "inline": True},
            {"name": "Open", "value": str(stats.get("open_positions", 0)), "inline": True},
            {"name": "Win Rate", "value": f"{stats.get('win_rate', 0):.0%}", "inline": True},
            {"name": "Realized P&L", "value": _fmt_pnl(pnl), "inline": True},
            {"name": "Unrealized", "value": _fmt_pnl(unrealized), "inline": True},
            {"name": "Today P&L", "value": _fmt_pnl(stats.get("today_pnl", 0)), "inline": True},
            {"name": "Margin Used", "value": f"${stats.get('total_margin_in_use', 0):.2f}", "inline": True},
            {"name": "Positions", "value": pos_text, "inline": False},
        ],
        "footer": {"text": f"Capital: ${INITIAL_CAPITAL:.0f}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await _send_discord(DISCORD_WEBHOOK_REPORTS, [embed])


async def notify_daily_report(
    stats: dict[str, Any],
    risk_data: dict[str, Any],
    recent_trades: list[dict],
    is_paper: bool = True,
) -> None:
    """Send daily report with multiple embeds to reports channel."""
    mode = "PAPER" if is_paper else "LIVE"
    pnl = stats.get("total_realized_pnl", 0)
    today_pnl = stats.get("today_pnl", 0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    win_rate = stats.get("win_rate", 0)

    if pnl > 0:
        main_color = 0x00C853
    elif pnl < 0:
        main_color = 0xFF1744
    else:
        main_color = 0x2196F3

    # Embed 1: Overview
    pnl_bar = _progress_bar(win_rate, 10)
    overview = {
        "title": f"Daily Report [{mode}] - {datetime.now().strftime('%Y-%m-%d')}",
        "description": (
            f"```\n"
            f" Capital    ${INITIAL_CAPITAL:.2f}\n"
            f" Total P&L  {_fmt_pnl(pnl)}\n"
            f" Today P&L  {_fmt_pnl(today_pnl)}\n"
            f"```"
        ),
        "color": main_color,
        "fields": [
            {"name": "Total Trades", "value": str(stats.get("total_trades", 0)), "inline": True},
            {"name": "Open", "value": str(stats.get("open_positions", 0)), "inline": True},
            {"name": "Closed", "value": str(stats.get("closed_positions", 0)), "inline": True},
            {"name": f"Win Rate {pnl_bar}", "value": f"**{wins}W** / **{losses}L** ({win_rate:.0%})", "inline": False},
        ],
    }

    # Embed 2: P&L Breakdown
    pnl_embed = {
        "color": main_color,
        "fields": [
            {"name": "Total Gains", "value": f"+${stats.get('total_gains', 0):.4f}", "inline": True},
            {"name": "Total Losses", "value": f"${stats.get('total_losses', 0):.4f}", "inline": True},
            {"name": "Unrealized", "value": _fmt_pnl(stats.get("unrealized_pnl", 0)), "inline": True},
            {"name": "Best Trade", "value": _fmt_pnl(stats.get("best_trade", 0)), "inline": True},
            {"name": "Worst Trade", "value": _fmt_pnl(stats.get("worst_trade", 0)), "inline": True},
            {"name": "Funding Paid", "value": f"${stats.get('total_funding_paid', 0):.4f}", "inline": True},
        ],
    }

    # Embed 3: Risk
    daily_stop = INITIAL_CAPITAL * RISK["daily_loss_limit_pct"]
    daily_usage = abs(risk_data.get("today_realized_pnl", 0)) / daily_stop * 100 if daily_stop > 0 else 0
    risk_bar = _progress_bar(min(daily_usage / 100, 1.0), 10)

    risk_embed = {
        "color": 0xFF9800 if daily_usage > 50 else 0x2196F3,
        "fields": [
            {"name": f"Daily Stop {risk_bar}", "value": f"{_fmt_pnl(risk_data.get('today_realized_pnl', 0))} / ${daily_stop:.2f} ({daily_usage:.0f}%)", "inline": False},
            {"name": "Margin Used", "value": f"${risk_data.get('total_margin', 0):.2f}", "inline": True},
            {"name": "Total Exposure", "value": f"${risk_data.get('total_exposure', 0):.2f}", "inline": True},
        ],
    }

    # Embed 4: Recent Trades
    if recent_trades:
        trade_lines = []
        for t in recent_trades:
            sym = t.get("symbol", "?")[:15]
            direction = t.get("direction", "?")
            rpnl = t.get("realized_pnl")
            if rpnl and rpnl > 0:
                icon = "+"
            elif rpnl and rpnl < 0:
                icon = "-"
            else:
                icon = "~"
            pnl_str = _fmt_pnl(rpnl) if rpnl is not None else "open"
            trade_lines.append(f"{icon} `{direction}` {sym} | {pnl_str}")
        trades_text = "\n".join(trade_lines)
    else:
        trades_text = "No recent trades"

    trades_embed = {
        "title": "Recent Trades",
        "description": trades_text,
        "color": 0x607D8B,
        "footer": {"text": f"Futures Bot v0.1.0 | {mode} Mode"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await _send_discord(DISCORD_WEBHOOK_REPORTS, [overview, pnl_embed, risk_embed, trades_embed])
