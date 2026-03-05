"""Scheduler - automated trading loop with APScheduler."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from config.settings import SCHEDULE, TRADING_MODE, INITIAL_CAPITAL
from config.profiles import ALL_PROFILES
from src.strategy.orchestrator import run_pipeline, run_multi_profile_pipeline
from src.trading.position_monitor import monitor_positions
from src.clients.binance_rest import BinanceClient
from src.db.models import (
    init_db, save_pnl_snapshot, get_trading_stats,
    get_open_positions, get_today_realized_pnl,
    get_recent_trades, get_risk_summary, get_peak_capital,
)
from src.notifications.notifier import (
    notify_daily_report, notify_daily_report_multi,
    notify_status, notify_status_multi,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _is_multi_profile() -> bool:
    return TRADING_MODE == "paper"


async def scan_job() -> None:
    """Periodic scan and trade execution."""
    logger.info("=== Scan cycle started ===")
    try:
        if _is_multi_profile():
            results = await run_multi_profile_pipeline()
            for r in results:
                logger.info(
                    "Scan [%s]: scanned=%d traded=%d",
                    r.profile_name, r.coins_scanned, r.trades_executed,
                )
        else:
            result = await run_pipeline()
            logger.info(
                "Scan cycle complete: scanned=%d traded=%d",
                result.coins_scanned, result.trades_executed,
            )
    except Exception as e:
        logger.error("Scan cycle failed: %s", e)


async def monitor_job() -> None:
    """Periodic position monitoring."""
    logger.info("=== Monitor cycle started ===")
    try:
        async with BinanceClient() as client:
            if _is_multi_profile():
                for profile in ALL_PROFILES:
                    await monitor_positions(client, profile=profile)
            else:
                await monitor_positions(client)
    except Exception as e:
        logger.error("Monitor cycle failed: %s", e)


async def status_job() -> None:
    """Periodic status update to Discord."""
    is_paper = TRADING_MODE == "paper"
    try:
        if _is_multi_profile():
            profiles_data = []
            for profile in ALL_PROFILES:
                stats = await get_trading_stats(is_paper=True, profile=profile.name)
                positions = await get_open_positions(is_paper=True, profile=profile.name)
                profiles_data.append({
                    "profile": profile.name,
                    "stats": stats,
                    "positions": positions,
                })
            await notify_status_multi(profiles_data)
        else:
            stats = await get_trading_stats(is_paper=is_paper)
            positions = await get_open_positions(is_paper=is_paper)
            await notify_status(stats, positions, is_paper=is_paper)
    except Exception as e:
        logger.error("Status update failed: %s", e)


async def daily_report_job() -> None:
    """Daily P&L report, snapshot, and Discord notification."""
    logger.info("=== Daily report ===")
    is_paper = TRADING_MODE == "paper"
    try:
        if _is_multi_profile():
            profiles_data = []
            for profile in ALL_PROFILES:
                stats = await get_trading_stats(is_paper=True, profile=profile.name)
                positions = await get_open_positions(is_paper=True, profile=profile.name)
                today_pnl = await get_today_realized_pnl(is_paper=True, profile=profile.name)
                risk_data = await get_risk_summary(is_paper=True, profile=profile.name)
                recent = await get_recent_trades(is_paper=True, profile=profile.name, limit=10)

                cum_pnl = stats["total_realized_pnl"]
                current_capital = INITIAL_CAPITAL + cum_pnl
                cum_pnl_pct = cum_pnl / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 0
                daily_pnl_pct = today_pnl / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 0

                peak = await get_peak_capital(is_paper=True, profile=profile.name)
                peak = max(peak, current_capital)
                drawdown = (peak - current_capital) / peak if peak > 0 else 0

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
                    is_paper=True,
                    profile=profile.name,
                )

                profiles_data.append({
                    "profile": profile.name,
                    "stats": stats,
                    "risk_data": risk_data,
                    "recent_trades": recent,
                    "positions": positions,
                })

            await notify_daily_report_multi(profiles_data)

            for pd in profiles_data:
                s = pd["stats"]
                logger.info(
                    "Daily [%s]: trades=%d open=%d pnl=$%.4f wr=%.1f%%",
                    pd["profile"], s["total_trades"], s["open_positions"],
                    s["total_realized_pnl"], s["win_rate"] * 100,
                )
        else:
            stats = await get_trading_stats(is_paper=is_paper)
            positions = await get_open_positions(is_paper=is_paper)
            today_pnl = await get_today_realized_pnl(is_paper=is_paper)
            risk_data = await get_risk_summary(is_paper=is_paper)
            recent = await get_recent_trades(is_paper=is_paper, limit=10)

            cum_pnl = stats["total_realized_pnl"]
            current_capital = INITIAL_CAPITAL + cum_pnl
            cum_pnl_pct = cum_pnl / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 0
            daily_pnl_pct = today_pnl / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 0

            peak = await get_peak_capital(is_paper=is_paper)
            peak = max(peak, current_capital)
            drawdown = (peak - current_capital) / peak if peak > 0 else 0

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
            )

            await notify_daily_report(
                stats=stats,
                risk_data=risk_data,
                recent_trades=recent,
                is_paper=is_paper,
            )

            logger.info(
                "Daily report: trades=%d open=%d pnl=$%.4f win_rate=%.1f%%",
                stats["total_trades"],
                stats["open_positions"],
                stats["total_realized_pnl"],
                stats["win_rate"] * 100,
            )
    except Exception as e:
        logger.error("Daily report failed: %s", e)


def start_scheduler() -> None:
    """Start the APScheduler daemon."""
    asyncio.run(_run_scheduler())


async def _run_scheduler() -> None:
    await init_db()

    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        scan_job,
        trigger=IntervalTrigger(minutes=SCHEDULE["scan_interval_minutes"]),
        id="scan",
        name="Coin scan & trade",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        monitor_job,
        trigger=IntervalTrigger(minutes=SCHEDULE["monitor_interval_minutes"]),
        id="monitor",
        name="Position monitor",
        misfire_grace_time=120,
    )

    scheduler.add_job(
        status_job,
        trigger=IntervalTrigger(minutes=SCHEDULE.get("status_interval_minutes", 10)),
        id="status",
        name="Discord status update",
        misfire_grace_time=120,
    )

    scheduler.add_job(
        daily_report_job,
        trigger=CronTrigger(
            hour=SCHEDULE["daily_report_hour"],
            minute=SCHEDULE["daily_report_minute"],
        ),
        id="daily_report",
        name="Daily P&L report",
        misfire_grace_time=3600,
    )

    mode = "PAPER" if TRADING_MODE == "paper" else "LIVE"
    multi = " (multi-profile)" if _is_multi_profile() else ""
    logger.info(
        "Scheduler started [%s mode%s] - scan every %dm, monitor every %dm, report at %02d:%02d",
        mode, multi,
        SCHEDULE["scan_interval_minutes"],
        SCHEDULE["monitor_interval_minutes"],
        SCHEDULE["daily_report_hour"],
        SCHEDULE["daily_report_minute"],
    )

    # Run initial scan immediately
    await scan_job()

    scheduler.start()

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
        scheduler.shutdown()


if __name__ == "__main__":
    start_scheduler()
