"""Orchestrator - coordinates scan -> analyze -> risk -> trade pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from config.settings import TRADING_MODE, INITIAL_CAPITAL
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
    coins_scanned: int = 0
    coins_analyzed: int = 0
    trades_executed: int = 0
    trades_skipped: int = 0
    error: str = ""
    details: list[dict[str, Any]] = field(default_factory=list)


async def run_pipeline(
    dry_run: bool = False,
    max_trades: int = 3,
) -> TradingResult:
    """Run the full trading pipeline.

    Pipeline: Scan -> Analyze -> Risk Check -> Trade

    Args:
        dry_run: If True, analyze only without trading
        max_trades: Maximum trades per cycle
    """
    is_paper = TRADING_MODE == "paper"
    result = TradingResult(success=False)

    await init_db()

    async with BinanceClient() as client:
        # -- Step 1: Scan Coins --
        logger.info("[1/4] Scanning coins...")
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
        logger.info("[2/4] Analyzing candidates...")
        actionable: list[dict[str, Any]] = []

        for candidate in candidates:
            try:
                analysis = await analyze_coin(client, candidate.symbol)
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
            "[2/4] %d/%d analyses are actionable",
            len(actionable), result.coins_analyzed,
        )

        if not actionable or dry_run:
            result.success = True
            if dry_run:
                logger.info("[3/4] Dry run - skipping risk check and trading")
            return result

        # -- Step 3: Risk Check --
        logger.info("[3/4] Running risk checks...")
        risk_mgr = RiskManager(client, capital=INITIAL_CAPITAL, is_paper=is_paper)

        # Calculate current drawdown for leverage adjustment
        stats = await get_trading_stats(is_paper=is_paper)
        current_capital = INITIAL_CAPITAL + stats["total_realized_pnl"]
        peak = await get_peak_capital(is_paper=is_paper)
        if peak < INITIAL_CAPITAL:
            peak = INITIAL_CAPITAL
        current_drawdown = (peak - current_capital) / peak if peak > 0 else 0

        approved: list[tuple[dict, RiskCheckResult]] = []

        for analysis in actionable:
            try:
                # Calculate leverage and position parameters
                volatility = analysis.get("volatility_24h", 0.03)
                leverage = calculate_leverage(
                    volatility_24h=volatility,
                    signal_strength=analysis["strength"],
                    current_drawdown_pct=current_drawdown,
                )

                position_params = calculate_position(
                    entry_price=analysis["close_price"],
                    atr=analysis.get("atr", 0) or 0,
                    direction=analysis["direction"],
                    leverage=leverage,
                    capital=current_capital,
                    volatility_24h=volatility,
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
                        "Risk rejected %s: %s",
                        analysis["symbol"],
                        risk_result.rejected_by,
                    )

            except Exception as e:
                logger.warning("Risk check failed for %s: %s", analysis["symbol"], e)

        logger.info(
            "[3/4] %d/%d passed risk checks",
            len(approved), len(actionable),
        )

        if not approved:
            result.success = True
            return result

        # -- Step 4: Execute Trades --
        logger.info("[4/4] Executing trades...")

        if is_paper:
            trader = PaperTrader()
        else:
            from src.trading.order_executor import OrderExecutor
            trader = OrderExecutor(client)

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
                        "Trade executed: %s %s %dx @ %.4f",
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
        "Pipeline complete: scanned=%d analyzed=%d traded=%d skipped=%d",
        result.coins_scanned,
        result.coins_analyzed,
        result.trades_executed,
        result.trades_skipped,
    )
    return result
