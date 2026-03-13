"""Orchestrator - coordinates scan -> analyze -> risk -> trade pipeline."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from config.settings import TRADING_MODE, INITIAL_CAPITAL
from config.profiles import ProfileConfig, ALL_PROFILES, NEUTRAL, get_profile
from src.clients.binance_rest import BinanceClient
from src.scanner.coin_scanner import scan_coins, CoinCandidate
from src.strategy.analyzer import analyze_coin
from src.risk.risk_manager import RiskManager, RiskCheckResult
from src.risk.leverage_calc import calculate_leverage, calculate_position, get_max_leverage
from src.trading.paper_trader import PaperTrader
from src.db.models import init_db, get_trading_stats, get_peak_capital
from src.notifications.notifier import notify_trade

logger = logging.getLogger(__name__)


@dataclass
class TradingResult:
    """Full pipeline execution result."""

    success: bool
    profile_name: str = "neutral"
    coins_scanned: int = 0
    coins_analyzed: int = 0
    trades_executed: int = 0
    trades_skipped: int = 0
    error: str = ""
    details: list[dict[str, Any]] = field(default_factory=list)


async def run_pipeline(
    dry_run: bool = False,
    max_trades: int = 3,
    profile: ProfileConfig | None = None,
    candidates: list[CoinCandidate] | None = None,
) -> TradingResult:
    """Run the full trading pipeline.

    Pipeline: Scan -> Analyze -> Risk Check -> Trade

    Args:
        dry_run: If True, analyze only without trading
        max_trades: Maximum trades per cycle
        profile: Trading profile (None = neutral defaults)
        candidates: Pre-scanned candidates (skip scan step if provided)
    """
    is_paper = TRADING_MODE == "paper"
    profile_name = profile.name if profile else "neutral"
    result = TradingResult(success=False, profile_name=profile_name)

    await init_db()

    async with BinanceClient() as client:
        # -- Step 1: Scan Coins --
        if candidates is not None:
            result.coins_scanned = len(candidates)
        else:
            logger.info("[1/4] Scanning coins... (profile=%s)", profile_name)
            try:
                candidates = await scan_coins(client, is_paper=is_paper)
                result.coins_scanned = len(candidates)
                logger.info("[1/4] Found %d candidate coins", len(candidates))
            except Exception as e:
                result.error = f"Scan failed: {e}"
                logger.error(result.error)
                return result

        if not candidates:
            result.success = True
            result.error = "No candidate coins found"
            return result

        # -- Step 2: Analyze Coins --
        logger.info("[2/4] Analyzing candidates... (profile=%s)", profile_name)
        actionable: list[dict[str, Any]] = []

        for candidate in candidates:
            try:
                analysis = await analyze_coin(client, candidate.symbol, profile=profile)
                if analysis is None:
                    continue

                result.coins_analyzed += 1
                result.details.append({
                    "symbol": analysis["symbol"],
                    "direction": analysis["direction"],
                    "strength": analysis["strength"],
                    "confirming": analysis["confirming_count"],
                    "actionable": analysis["is_actionable"],
                })

                if analysis["is_actionable"]:
                    # Attach scanner data for leverage/risk calculations
                    analysis["volatility_24h"] = candidate.volatility_24h
                    analysis["funding_rate"] = candidate.funding_rate
                    actionable.append(analysis)

            except Exception as e:
                logger.warning("Analysis failed for %s: %s", candidate.symbol, e)

        logger.info(
            "[2/4] %d/%d analyses are actionable (profile=%s)",
            len(actionable), result.coins_analyzed, profile_name,
        )

        if not actionable or dry_run:
            result.success = True
            if dry_run:
                logger.info("[3/4] Dry run - skipping risk check and trading")
            return result

        # -- Step 3: Risk Check --
        logger.info("[3/4] Running risk checks... (profile=%s)", profile_name)

        # Calculate current capital (compound growth) and drawdown
        stats = await get_trading_stats(is_paper=is_paper, profile=profile_name)
        current_capital = INITIAL_CAPITAL + stats["total_realized_pnl"]
        peak = await get_peak_capital(is_paper=is_paper, profile=profile_name)
        if peak < INITIAL_CAPITAL:
            peak = INITIAL_CAPITAL
        current_drawdown = (peak - current_capital) / peak if peak > 0 else 0

        risk_mgr = RiskManager(
            client, capital=current_capital, is_paper=is_paper, profile=profile,
        )

        approved: list[tuple[dict, RiskCheckResult]] = []

        for analysis in actionable:
            try:
                # Calculate leverage and position parameters
                volatility = analysis.get("volatility_24h", 0.03)
                leverage = calculate_leverage(
                    volatility_24h=volatility,
                    signal_strength=analysis["strength"],
                    current_drawdown_pct=current_drawdown,
                    profile=profile,
                )

                position_params = calculate_position(
                    entry_price=analysis["close_price"],
                    atr=analysis.get("atr", 0) or 0,
                    direction=analysis["direction"],
                    leverage=leverage,
                    capital=current_capital,
                    volatility_24h=volatility,
                    profile=profile,
                )

                if position_params.position_size <= 0:
                    result.trades_skipped += 1
                    continue

                risk_result = await risk_mgr.check(
                    symbol=analysis["symbol"],
                    direction=analysis["direction"],
                    signal_strength=analysis["strength"],
                    position_params=position_params,
                    funding_rate=analysis.get("funding_rate", 0),
                )

                if risk_result.passed:
                    analysis["position_params"] = position_params
                    approved.append((analysis, risk_result))
                else:
                    result.trades_skipped += 1
                    logger.info(
                        "Risk rejected %s: %s (profile=%s)",
                        analysis["symbol"],
                        risk_result.rejected_by,
                        profile_name,
                    )

            except Exception as e:
                logger.warning("Risk check failed for %s: %s", analysis["symbol"], e)

        logger.info(
            "[3/4] %d/%d passed risk checks (profile=%s)",
            len(approved), len(actionable), profile_name,
        )

        if not approved:
            result.success = True
            return result

        # -- Step 4: Execute Trades --
        logger.info("[4/4] Executing trades... (profile=%s)", profile_name)

        if is_paper:
            trader = PaperTrader(profile_name=profile_name)
        else:
            from src.trading.order_executor import OrderExecutor
            trader = OrderExecutor(client, profile_name=profile_name)

        trades_done = 0
        for analysis, risk_result in approved:
            if trades_done >= max_trades:
                break

            params = analysis["position_params"]
            try:
                trade_result = await trader.place_order(
                    symbol=analysis["symbol"],
                    direction=analysis["direction"],
                    entry_price=analysis["close_price"],
                    params=params,
                    signal_id=analysis.get("signal_id"),
                )
                if trade_result["success"]:
                    result.trades_executed += 1
                    trades_done += 1
                    logger.info(
                        "Trade executed [%s]: %s %s %dx @ %.4f",
                        profile_name,
                        analysis["direction"],
                        analysis["symbol"],
                        params.leverage,
                        analysis["close_price"],
                    )
                    await notify_trade(trade_result)
                else:
                    result.trades_skipped += 1
                    logger.warning("Trade failed: %s", trade_result.get("error"))
            except Exception as e:
                result.trades_skipped += 1
                logger.error("Trade execution error: %s", e)

    result.success = True
    logger.info(
        "Pipeline complete [%s]: scanned=%d analyzed=%d traded=%d skipped=%d",
        profile_name,
        result.coins_scanned,
        result.coins_analyzed,
        result.trades_executed,
        result.trades_skipped,
    )
    return result


async def run_multi_profile_pipeline(
    dry_run: bool = False,
    max_trades: int = 3,
    profiles: list[ProfileConfig] | None = None,
) -> list[TradingResult]:
    """Run the pipeline for multiple profiles simultaneously.

    Scans coins once, then runs analyze -> risk -> trade in parallel per profile.

    Args:
        dry_run: If True, analyze only without trading
        max_trades: Maximum trades per cycle per profile
        profiles: Profiles to run (default: ALL_PROFILES)

    Returns:
        List of TradingResult, one per profile.
    """
    if profiles is None:
        profiles = list(ALL_PROFILES)

    is_paper = TRADING_MODE == "paper"
    if not is_paper:
        logger.warning("Multi-profile is only supported in paper mode. Running neutral only.")
        return [await run_pipeline(dry_run=dry_run, max_trades=max_trades, profile=NEUTRAL)]

    await init_db()

    # Step 1: Scan once (shared across all profiles)
    logger.info("=== Multi-profile scan: %d profiles ===", len(profiles))
    async with BinanceClient() as client:
        try:
            candidates = await scan_coins(client, is_paper=True)
            logger.info("Scanned %d candidate coins for all profiles", len(candidates))
        except Exception as e:
            logger.error("Multi-profile scan failed: %s", e)
            return [
                TradingResult(success=False, profile_name=p.name, error=str(e))
                for p in profiles
            ]

    # Step 2: Run per-profile pipelines concurrently
    async def _run_profile(prof: ProfileConfig) -> TradingResult:
        try:
            return await run_pipeline(
                dry_run=dry_run,
                max_trades=max_trades,
                profile=prof,
                candidates=candidates,
            )
        except Exception as e:
            logger.error("Pipeline failed for profile %s: %s", prof.name, e)
            return TradingResult(
                success=False, profile_name=prof.name, error=str(e),
            )

    results = await asyncio.gather(*[_run_profile(p) for p in profiles])

    for r in results:
        logger.info(
            "Profile [%s]: traded=%d skipped=%d success=%s",
            r.profile_name, r.trades_executed, r.trades_skipped, r.success,
        )

    return list(results)
