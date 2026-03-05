"""Scalping process entry point - WebSocket event-driven 3m trading."""

from __future__ import annotations

import asyncio
import logging

from config.profiles import get_profile
from config.settings import SCALP_SETTINGS
from src.db.models import init_db
from src.scalping.watcher import ScalpWatcher
from src.scalping.pipeline import ScalpPipeline
from src.scalping.monitor import ScalpMonitor

logger = logging.getLogger(__name__)


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


async def run_scalp() -> None:
    """Run the scalping system.

    Launches 3 concurrent coroutines:
    1. ScalpWatcher - WebSocket miniTicker spike detection
    2. ScalpMonitor - WebSocket markPrice position monitoring
    3. Hot coin REST polling (every 3 minutes)
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
        )
    except asyncio.CancelledError:
        logger.info("Scalp runner shutting down")
    finally:
        await watcher.close()
        await monitor.close()
