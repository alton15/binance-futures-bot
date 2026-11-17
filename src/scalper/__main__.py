"""CLI: python -m src.scalper [--mode paper] [--log-level INFO]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from contextlib import AsyncExitStack

from src.scalper.config import ConfigError, load_settings
from src.scalper.engine import Engine
from src.scalper.execution.exchange import Exchange
from src.scalper.notify import Notifier
from src.scalper.storage import Storage

logger = logging.getLogger("scalper")
NOISY_LOGGERS = ("websockets", "aiosqlite", "ccxt", "httpx", "httpcore", "asyncio")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="scalper", description="Binance USDT-M event-driven scalper")
    p.add_argument("--mode", choices=["paper", "live"], default=None,
                   help="override TRADING_MODE (live is currently refused)")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--reset-drawdown", action="store_true",
                   help="clear a max-drawdown halt by resetting the peak to current equity, then exit")
    return p.parse_args(argv)


async def _reset_drawdown(storage: Storage, initial_capital: float) -> int:
    equity = initial_capital + await storage.realized_pnl()
    await storage.snapshot_equity(time.time(), equity, equity)
    logger.info("drawdown peak reset to current equity $%.2f", equity)
    return 0


async def _run_engine(engine: Engine, notifier: Notifier) -> int:
    task = asyncio.create_task(engine.run())
    loop = asyncio.get_running_loop()

    def _request_stop(signame: str) -> None:
        logger.info("%s received, stopping", signame)
        task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop, sig.name)
    try:
        await task
    except asyncio.CancelledError:
        logger.info("shutdown complete")
    except Exception:
        logger.exception("engine crashed")
        await notifier.alert("engine crashed - check logs")
        return 1
    return 0


async def _amain(mode: str | None, reset_drawdown: bool) -> int:
    try:
        settings = load_settings(mode)
    except ConfigError as e:
        logger.error("config error: %s", e)
        return 2
    if not settings.is_paper:
        logger.error("live mode is disabled: no validated edge yet (see README). Run with --mode paper.")
        return 2
    async with AsyncExitStack() as stack:
        storage = Storage(settings.db_path, settings.mode)
        await storage.open()
        stack.push_async_callback(storage.close)
        if reset_drawdown:
            return await _reset_drawdown(storage, settings.initial_capital)
        exchange = Exchange(settings)
        stack.push_async_callback(exchange.close)
        notifier = Notifier(settings.discord_alerts, settings.discord_reports, f"scalper-{settings.mode}")
        stack.push_async_callback(notifier.close)
        return await _run_engine(Engine(settings, exchange, storage, notifier), notifier)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in NOISY_LOGGERS:  # per-frame debug logs would starve the event loop
        logging.getLogger(name).setLevel(logging.WARNING)
    return asyncio.run(_amain(args.mode, args.reset_drawdown))


if __name__ == "__main__":
    sys.exit(main())
