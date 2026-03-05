"""Scalping process entry point - WebSocket event-driven 3m trading."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from config.profiles import get_profile
from config.settings import (
    SCALP_SETTINGS, SCHEDULE, TRADING_MODE, INITIAL_CAPITAL,
)
from src.db.models import (
    init_db, get_trading_stats, get_open_positions,
    get_today_realized_pnl, get_risk_summary,
    get_recent_trades, get_peak_capital, save_pnl_snapshot,
)
from src.notifications.notifier import notify_status, notify_daily_report
from src.scalping.watcher import ScalpWatcher
from src.scalping.pipeline import ScalpPipeline
from src.scalping.monitor import ScalpMonitor

logger = logging.getLogger(__name__)

PROFILE_NAME = "scalp"


async def _periodic_hot_coin_poll(watcher: ScalpWatcher) -> None:
    """Poll Binance for hot coins every N seconds."""
    interval = SCALP_SETTINGS["hot_coin_poll_seconds"]
    logger.info("Hot coin polling started (interval=%ds)", interval)
    try:
        while True:
            await asyncio.sleep(interval)
            await watcher.poll_hot_coins()
    except asyncio.CancelledError:
        logger.info("Hot coin polling stopped")


async def _periodic_status_report() -> None:
    """Send status report to Discord every 30 minutes."""
    interval = SCHEDULE.get("status_interval_minutes", 30) * 60
    is_paper = TRADING_MODE == "paper"
    logger.info("Scalp status reporting started (interval=%dm)", interval // 60)
    try:
        # Wait before first report to allow some data to accumulate
        await asyncio.sleep(interval)
        while True:
            try:
                stats = await get_trading_stats(
                    is_paper=is_paper, profile=PROFILE_NAME,
                )
                positions = await get_open_positions(
                    is_paper=is_paper, profile=PROFILE_NAME,
                )
                await notify_status(
                    stats, positions,
                    is_paper=is_paper, profile_name=PROFILE_NAME,
                )
                logger.info(
                    "Scalp status sent: trades=%d open=%d pnl=$%.4f",
                    stats["total_trades"], stats["open_positions"],
                    stats["total_realized_pnl"],
                )
            except Exception as e:
                logger.error("Scalp status report failed: %s", e)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Scalp status reporting stopped")


async def _daily_report_job() -> None:
    """Send daily P&L report and save PnL snapshot at configured time."""
    report_hour = SCHEDULE.get("daily_report_hour", 23)
    report_minute = SCHEDULE.get("daily_report_minute", 0)
    is_paper = TRADING_MODE == "paper"
    logger.info("Scalp daily report scheduled at %02d:%02d", report_hour, report_minute)
    try:
        while True:
            # Calculate seconds until next report time
            now = datetime.now(timezone.utc)
            target = now.replace(
                hour=report_hour, minute=report_minute, second=0, microsecond=0,
            )
            if target <= now:
                # Already past today's report time, schedule for tomorrow
                target = target.replace(day=target.day + 1)
            wait_seconds = (target - now).total_seconds()
            logger.debug("Next scalp daily report in %.0f seconds", wait_seconds)
            await asyncio.sleep(wait_seconds)

            try:
                await _send_daily_report(is_paper)
            except Exception as e:
                logger.error("Scalp daily report failed: %s", e)
    except asyncio.CancelledError:
        logger.info("Scalp daily report job stopped")


async def _send_daily_report(is_paper: bool) -> None:
    """Generate and send the daily report for scalp profile."""
    stats = await get_trading_stats(is_paper=is_paper, profile=PROFILE_NAME)
    positions = await get_open_positions(is_paper=is_paper, profile=PROFILE_NAME)
    today_pnl = await get_today_realized_pnl(is_paper=is_paper, profile=PROFILE_NAME)
    risk_data = await get_risk_summary(is_paper=is_paper, profile=PROFILE_NAME)
    recent = await get_recent_trades(
        is_paper=is_paper, profile=PROFILE_NAME, limit=10,
    )

    cum_pnl = stats["total_realized_pnl"]
    current_capital = INITIAL_CAPITAL + cum_pnl
    cum_pnl_pct = cum_pnl / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 0
    daily_pnl_pct = today_pnl / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 0

    peak = await get_peak_capital(is_paper=is_paper, profile=PROFILE_NAME)
    peak = max(peak, current_capital)
    drawdown = (peak - current_capital) / peak if peak > 0 else 0

    # Save PnL snapshot
    await save_pnl_snapshot(
        total_capital=current_capital,
        daily_pnl=today_pnl,
        daily_pnl_pct=daily_pnl_pct,
        cumulative_pnl=cum_pnl,
        cumulative_pnl_pct=cum_pnl_pct,
        peak_capital=peak,
        drawdown_pct=drawdown,
        open_positions=len(positions),
        total_trades=stats["total_trades"],
        win_rate=stats["win_rate"],
        is_paper=is_paper,
        profile=PROFILE_NAME,
    )

    # Send Discord report
    await notify_daily_report(
        stats=stats,
        risk_data=risk_data,
        recent_trades=recent,
        is_paper=is_paper,
        profile_name=PROFILE_NAME,
    )

    logger.info(
        "Scalp daily report: trades=%d open=%d pnl=$%.4f wr=%.1f%%",
        stats["total_trades"], stats["open_positions"],
        stats["total_realized_pnl"], stats["win_rate"] * 100,
    )


async def run_scalp() -> None:
    """Run the scalping system.

    Launches 5 concurrent coroutines:
    1. ScalpWatcher - WebSocket miniTicker spike detection
    2. ScalpMonitor - WebSocket markPrice position monitoring
    3. Hot coin REST polling (every 3 minutes)
    4. Status report (every 30 minutes)
    5. Daily P&L report (23:00 UTC)
    """
    await init_db()

    profile = get_profile("scalp")
    logger.info("Starting scalp runner (profile=%s)", profile.label)

    watcher = ScalpWatcher(profile_name=profile.name)
    pipeline = ScalpPipeline(profile=profile)
    monitor = ScalpMonitor(profile=profile)

    try:
        await asyncio.gather(
            watcher.run(on_spike=pipeline.on_spike_event),
            monitor.run(),
            _periodic_hot_coin_poll(watcher),
            _periodic_status_report(),
            _daily_report_job(),
        )
    except asyncio.CancelledError:
        logger.info("Scalp runner shutting down")
    finally:
        await watcher.close()
        await monitor.close()
