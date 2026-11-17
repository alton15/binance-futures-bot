"""CLI: python -m src.trend --profile trend_lo [--mode paper] [--reset-drawdown]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from contextlib import AsyncExitStack

from src.scalper.execution.exchange import Exchange
from src.scalper.notify import Notifier
from src.trend.config import ConfigError, load_settings
from src.trend.engine import TrendEngine
from src.trend.profiles import PROFILES, get_profile
from src.trend.storage import TrendStorage

logger = logging.getLogger("trend")
NOISY_LOGGERS = ("websockets", "aiosqlite", "ccxt", "httpx", "httpcore", "asyncio", "numba")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="trend", description="Binance USDT-M multi-asset trend follower")
    p.add_argument("--profile", choices=sorted(PROFILES), default="trend_lo")
    p.add_argument("--mode", choices=["paper", "live"], default=None,
                   help="override TRADING_MODE (live is currently refused)")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--reset-drawdown", action="store_true",
                   help="clear a drawdown halt (peak := latest equity), then exit")
    return p.parse_args(argv)


async def _reset_drawdown(storage: TrendStorage) -> int:
    rows = await storage.db.execute_fetchall("SELECT equity, cash, gross, n_pos FROM equity "
                                             "ORDER BY ts DESC, rowid DESC LIMIT 1")
    if not rows:
        logger.info("no equity history; nothing to reset")
        return 0
    equity, cash, gross, n_pos = rows[0]
    await storage.snapshot_equity(time.time(), equity, cash, gross, n_pos, equity)
    await storage.set_meta("halted", "0")
    logger.info("drawdown halt cleared, peak reset to $%.2f", equity)
    return 0


async def _run_engine(engine: TrendEngine, notifier: Notifier) -> int:
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
        logger.exception("trend engine crashed")
        await notifier.alert("trend engine crashed - check logs")
        return 1
    return 0


async def _amain(profile_name: str, mode: str | None, reset_drawdown: bool) -> int:
    try:
        settings = load_settings(get_profile(profile_name), mode)
    except (ConfigError, ValueError) as e:
        logger.error("config error: %s", e)
        return 2
    if not settings.is_paper:
        logger.error("live mode is disabled: trend profiles are paper-only until validated. Use --mode paper.")
        return 2
    logger.info("trend %s paper, capital $%.2f, db %s", profile_name, settings.initial_capital, settings.db_path)
    async with AsyncExitStack() as stack:
        storage = TrendStorage(settings.db_path)
        await storage.open()
        stack.push_async_callback(storage.close)
        if reset_drawdown:
            return await _reset_drawdown(storage)
        exchange = Exchange(settings)  # type: ignore[arg-type]  # duck-typed: is_paper/api_key/api_secret/testnet
        stack.push_async_callback(exchange.close)
        notifier = Notifier(settings.discord_alerts, settings.discord_reports, f"{profile_name}-{settings.mode}")
        stack.push_async_callback(notifier.close)
        return await _run_engine(TrendEngine(settings, exchange, storage, notifier), notifier)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return asyncio.run(_amain(args.profile, args.mode, args.reset_drawdown))


if __name__ == "__main__":
    sys.exit(main())
