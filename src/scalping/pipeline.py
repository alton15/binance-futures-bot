"""ScalpPipeline - spike event → analyze → risk → trade pipeline."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.settings import SCALP_SETTINGS, TRADING_MODE, INITIAL_CAPITAL
from config.profiles import ProfileConfig, get_profile
from src.clients.binance_rest import BinanceClient
from src.strategy.analyzer import analyze_coin
from src.risk.risk_manager import RiskManager
from src.risk.leverage_calc import calculate_leverage, calculate_position
from src.trading.paper_trader import PaperTrader
from src.db.models import get_trading_stats, get_peak_capital
from src.notifications.notifier import notify_trade
from src.scalping.watcher import SpikeEvent

logger = logging.getLogger(__name__)


class ScalpPipeline:
    """Scalping analysis and trade execution pipeline.

    Reuses all existing modules (analyzer, risk manager, leverage calc,
    paper trader, order executor) with scalp-specific timeframes and profile.
    """

    def __init__(self, profile: ProfileConfig | None = None) -> None:
        self._profile = profile or get_profile("scalp")
        self._primary_tf = SCALP_SETTINGS["primary_timeframe"]
        self._confirm_tfs = SCALP_SETTINGS["confirm_timeframes"]
        self._max_concurrent = SCALP_SETTINGS["max_concurrent_analyses"]

        # Semaphore to limit concurrent analyses
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        # Track active analyses to avoid duplicates
        self._active_symbols: set[str] = set()

    async def on_spike_event(self, event: SpikeEvent) -> None:
        """Handle a spike event from ScalpWatcher.

        Runs the full pipeline: analyze → risk check → trade.
        """
        symbol = event.symbol

        # Skip if already analyzing this symbol
        if symbol in self._active_symbols:
            logger.debug("Already analyzing %s, skipping", symbol)
            return

        # Convert bare symbol to ccxt format (e.g. BTCUSDT → BTC/USDT:USDT)
        ccxt_symbol = self._to_ccxt_symbol(symbol)

        self._active_symbols.add(symbol)
        try:
            async with self._semaphore:
                await self._run_analysis(ccxt_symbol, event)
        finally:
            self._active_symbols.discard(symbol)

    async def _run_analysis(self, symbol: str, event: SpikeEvent) -> None:
        """Execute the full scalp pipeline for a single symbol."""
        is_paper = TRADING_MODE == "paper"
        profile = self._profile
        profile_name = profile.name

        logger.info(
            "ScalpPipeline: analyzing %s (trigger=%s, magnitude=%.4f)",
            symbol, event.trigger_type, event.magnitude,
        )

        async with BinanceClient() as client:
            # --- Step 1: Analyze with primary timeframe (3m) ---
            analysis = await analyze_coin(
                client, symbol,
                timeframe=self._primary_tf,
                profile=profile,
                confirm_timeframes=self._confirm_tfs,
            )
            if analysis is None:
                logger.debug("Analysis returned None for %s", symbol)
                return

            if not analysis["is_actionable"]:
                logger.info(
                    "Not actionable: %s %s str=%.2f conf=%d mtf=%d",
                    symbol, analysis["direction"], analysis["strength"],
                    analysis["confirming_count"], analysis["mtf_confirms"],
                )
                return

            # --- Step 2: Estimate volatility and funding rate ---
            # Use ATR/Price ratio from analysis (already computed from OHLCV)
            atr_val = analysis.get("atr", 0) or 0
            close_val = analysis.get("close_price", 0) or 0
            volatility_24h = max(atr_val / close_val, 0.01) if close_val > 0 else 0.03

            try:
                ticker = await client.fetch_ticker(symbol)
                vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                funding = await client.fetch_funding_rate(symbol)
                funding_rate = float(funding.get("fundingRate", 0) or 0)
            except Exception as e:
                logger.warning("Failed to fetch ticker data for %s: %s", symbol, e)
                funding_rate = 0
                vol_24h = event.volume_24h

            # --- Step 3: Calculate leverage and position ---
            stats = await get_trading_stats(is_paper=is_paper, profile=profile_name)
            current_capital = INITIAL_CAPITAL + stats["total_realized_pnl"]
            peak = await get_peak_capital(is_paper=is_paper, profile=profile_name)
            if peak < INITIAL_CAPITAL:
                peak = INITIAL_CAPITAL
            current_drawdown = (peak - current_capital) / peak if peak > 0 else 0

            leverage = calculate_leverage(
                volatility_24h=volatility_24h,
                signal_strength=analysis["strength"],
                current_drawdown_pct=current_drawdown,
                profile=profile,
            )

            atr = analysis.get("atr", 0) or 0
            position_params = calculate_position(
                entry_price=analysis["close_price"],
                atr=atr,
                direction=analysis["direction"],
                leverage=leverage,
                capital=current_capital,
                volatility_24h=volatility_24h,
                profile=profile,
            )

            if position_params.position_size <= 0:
                logger.info("Position size zero for %s, skipping", symbol)
                return

            # --- Step 4: Risk check ---
            risk_mgr = RiskManager(
                client, capital=current_capital, is_paper=is_paper, profile=profile,
            )
            risk_result = await risk_mgr.check(
                symbol=symbol,
                direction=analysis["direction"],
                signal_strength=analysis["strength"],
                position_params=position_params,
                funding_rate=funding_rate,
            )

            if not risk_result.passed:
                logger.info(
                    "Risk rejected %s: %s", symbol, risk_result.rejected_by,
                )
                return

            # --- Step 5: Execute trade ---
            if is_paper:
                trader = PaperTrader(profile_name=profile_name)
            else:
                from src.trading.order_executor import OrderExecutor
                trader = OrderExecutor(client)

            trade_result = await trader.place_order(
                symbol=symbol,
                direction=analysis["direction"],
                entry_price=analysis["close_price"],
                params=position_params,
                signal_id=analysis.get("signal_id"),
            )

            if trade_result.get("success"):
                logger.info(
                    "SCALP TRADE [%s]: %s %s %dx @ %.4f (trigger=%s)",
                    profile_name, analysis["direction"], symbol,
                    position_params.leverage, analysis["close_price"],
                    event.trigger_type,
                )
                await notify_trade(trade_result)
            else:
                logger.warning(
                    "Scalp trade failed for %s: %s",
                    symbol, trade_result.get("error"),
                )

    @staticmethod
    def _to_ccxt_symbol(bare_symbol: str) -> str:
        """Convert bare symbol (BTCUSDT) to ccxt format (BTC/USDT:USDT)."""
        if "/" in bare_symbol:
            return bare_symbol
        # Strip trailing USDT and reconstruct
        if bare_symbol.endswith("USDT"):
            base = bare_symbol[:-4]
            return f"{base}/USDT:USDT"
        return bare_symbol
