"""Risk manager - 10-gate sequential risk check before any trade."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from config.settings import RISK, INITIAL_CAPITAL, TRADING_MODE
from src.clients.binance_rest import BinanceClient
from src.risk.leverage_calc import PositionParams
from src.db.models import (
    get_open_positions,
    has_position_for_symbol,
    get_today_realized_pnl,
    get_trading_stats,
    get_peak_capital,
)

if TYPE_CHECKING:
    from config.profiles import ProfileConfig

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Result of a risk check."""

    passed: bool
    gate_results: list[dict[str, Any]] = field(default_factory=list)
    rejected_by: str = ""

    def add_gate(self, name: str, passed: bool, reason: str = "") -> None:
        self.gate_results.append({"name": name, "passed": passed, "reason": reason})
        if not passed and not self.rejected_by:
            self.rejected_by = name
            self.passed = False


class RiskManager:
    """10-gate risk manager for futures trading.

    Gates:
    1. Signal strength threshold
    2. Max open positions
    3. No duplicate symbol
    4. Daily loss limit
    5. Max drawdown
    6. Available margin check
    7. Total exposure limit
    8. Leverage tier validation
    9. Liquidation buffer (30%+ distance)
    10. Funding rate check (< 0.1%)
    """

    def __init__(
        self,
        client: BinanceClient,
        capital: float = INITIAL_CAPITAL,
        is_paper: bool = True,
        profile: ProfileConfig | None = None,
    ) -> None:
        self.client = client
        self.capital = capital
        self.is_paper = is_paper
        self.profile = profile
        self._profile_name = profile.name if profile else "neutral"

    def _risk(self, key: str) -> Any:
        """Get risk parameter from profile or global RISK."""
        if self.profile:
            return self.profile.get_risk(key)
        return RISK[key]

    async def check(
        self,
        symbol: str,
        direction: str,
        signal_strength: float,
        position_params: PositionParams,
        funding_rate: float = 0,
    ) -> RiskCheckResult:
        """Run all 10 risk gates sequentially."""
        result = RiskCheckResult(passed=True)

        # Gate 1: Signal strength
        self._gate_signal_strength(signal_strength, result)
        if not result.passed:
            return result

        # Gate 2: Position limit
        await self._gate_position_limit(result)
        if not result.passed:
            return result

        # Gate 3: No duplicate
        await self._gate_no_duplicate(symbol, result)
        if not result.passed:
            return result

        # Gate 4: Daily loss limit
        await self._gate_daily_loss(result)
        if not result.passed:
            return result

        # Gate 5: Max drawdown
        await self._gate_max_drawdown(result)
        if not result.passed:
            return result

        # Gate 6: Available margin
        await self._gate_available_margin(position_params.margin_required, result)
        if not result.passed:
            return result

        # Gate 7: Total exposure (margin-based)
        await self._gate_total_exposure(position_params.margin_required, result)
        if not result.passed:
            return result

        # Gate 8: Leverage validation
        self._gate_leverage_valid(position_params.leverage, result)

        # Gate 9: Liquidation buffer (entry to liquidation distance)
        entry_price_est = (
            position_params.notional_value / position_params.position_size
            if position_params.position_size > 0 else 0
        )
        self._gate_liquidation_buffer(
            direction, position_params.liquidation_price,
            entry_price_est, result,
        )
        if not result.passed:
            return result

        # Gate 10: Funding rate
        self._gate_funding_rate(funding_rate, result)

        if result.passed:
            logger.info(
                "Risk check PASSED for %s %s (strength=%.2f, profile=%s)",
                symbol, direction, signal_strength, self._profile_name,
            )
        else:
            logger.info(
                "Risk check REJECTED by '%s' for %s (profile=%s)",
                result.rejected_by, symbol, self._profile_name,
            )

        return result

    def _gate_signal_strength(
        self, strength: float, result: RiskCheckResult
    ) -> None:
        """Gate 1: Signal strength must exceed minimum."""
        threshold = self._risk("signal_strength_min")
        passed = strength >= threshold
        result.add_gate(
            "signal_strength",
            passed,
            f"Strength {strength:.2f} {'>='}  {threshold:.2f}"
            if passed else
            f"Strength {strength:.2f} < {threshold:.2f}",
        )

    async def _gate_position_limit(self, result: RiskCheckResult) -> None:
        """Gate 2: Maximum open positions."""
        positions = await get_open_positions(
            is_paper=self.is_paper, profile=self._profile_name,
        )
        count = len(positions)
        max_pos = self._risk("max_open_positions")
        passed = count < max_pos
        result.add_gate(
            "position_limit",
            passed,
            f"Open positions: {count}/{max_pos}",
        )

    async def _gate_no_duplicate(
        self, symbol: str, result: RiskCheckResult
    ) -> None:
        """Gate 3: No duplicate position for same symbol."""
        has_pos = await has_position_for_symbol(
            symbol, is_paper=self.is_paper, profile=self._profile_name,
        )
        passed = not has_pos
        result.add_gate(
            "no_duplicate",
            passed,
            "No existing position" if passed else "Duplicate position exists",
        )

    async def _gate_daily_loss(self, result: RiskCheckResult) -> None:
        """Gate 4: Daily loss limit check (uses dynamic capital like Gates 5/6)."""
        daily_pnl = await get_today_realized_pnl(
            is_paper=self.is_paper, profile=self._profile_name,
        )
        stats = await get_trading_stats(
            is_paper=self.is_paper, profile=self._profile_name,
        )
        current_capital = self.capital + stats["total_realized_pnl"]
        max_daily_loss = -current_capital * self._risk("daily_loss_limit_pct")
        passed = daily_pnl > max_daily_loss
        result.add_gate(
            "daily_loss_limit",
            passed,
            f"Daily P&L: ${daily_pnl:.2f} (limit: ${max_daily_loss:.2f})"
            if passed else
            f"Daily loss limit hit: ${daily_pnl:.2f} <= ${max_daily_loss:.2f}",
        )

    async def _gate_max_drawdown(self, result: RiskCheckResult) -> None:
        """Gate 5: Maximum drawdown from peak."""
        stats = await get_trading_stats(
            is_paper=self.is_paper, profile=self._profile_name,
        )
        total_pnl = stats["total_realized_pnl"]
        current_capital = self.capital + total_pnl
        peak = await get_peak_capital(
            is_paper=self.is_paper, profile=self._profile_name,
        )
        if peak < self.capital:
            peak = self.capital

        drawdown = (peak - current_capital) / peak if peak > 0 else 0
        max_dd = self._risk("max_drawdown_pct")
        passed = drawdown < max_dd
        result.add_gate(
            "max_drawdown",
            passed,
            f"Drawdown: {drawdown:.1%} (max: {max_dd:.1%})"
            if passed else
            f"Max drawdown exceeded: {drawdown:.1%} >= {max_dd:.1%}",
        )

    async def _gate_available_margin(
        self, required_margin: float, result: RiskCheckResult
    ) -> None:
        """Gate 6: Available margin check."""
        positions = await get_open_positions(
            is_paper=self.is_paper, profile=self._profile_name,
        )
        used_margin = sum(p.get("margin", 0) for p in positions)
        stats = await get_trading_stats(
            is_paper=self.is_paper, profile=self._profile_name,
        )
        current_capital = self.capital + stats["total_realized_pnl"]
        available = current_capital - used_margin

        passed = available >= required_margin
        result.add_gate(
            "available_margin",
            passed,
            f"Available: ${available:.2f} (need: ${required_margin:.2f})"
            if passed else
            f"Insufficient margin: ${available:.2f} < ${required_margin:.2f}",
        )

    async def _gate_total_exposure(
        self, new_margin: float, result: RiskCheckResult
    ) -> None:
        """Gate 7: Total margin exposure limit."""
        positions = await get_open_positions(
            is_paper=self.is_paper, profile=self._profile_name,
        )
        used_margin = sum(p.get("margin", 0) for p in positions)
        stats = await get_trading_stats(
            is_paper=self.is_paper, profile=self._profile_name,
        )
        current_capital = self.capital + stats["total_realized_pnl"]

        max_exposure = self._risk("max_exposure_pct")
        exposure_cap = current_capital * max_exposure
        min_remaining = current_capital * 0.05

        total_margin = used_margin + new_margin
        remaining = exposure_cap - used_margin

        if remaining < min_remaining:
            result.add_gate(
                "total_exposure", False,
                f"Remaining capacity too small: ${remaining:.2f} < ${min_remaining:.2f}",
            )
            return

        passed = total_margin <= exposure_cap
        result.add_gate(
            "total_exposure",
            passed,
            f"Margin: ${total_margin:.2f} / ${exposure_cap:.2f}"
            if passed else
            f"Margin limit: ${total_margin:.2f} > ${exposure_cap:.2f}",
        )

    def _gate_leverage_valid(
        self, leverage: int, result: RiskCheckResult
    ) -> None:
        """Gate 8: Leverage within allowed range."""
        lev_min = self.profile.leverage_min if self.profile else 2
        lev_max = self.profile.leverage_max if self.profile else 8
        passed = lev_min <= leverage <= lev_max
        result.add_gate(
            "leverage_valid",
            passed,
            f"Leverage: {leverage}x (range: {lev_min}-{lev_max}x)",
        )

    def _gate_liquidation_buffer(
        self,
        direction: str,
        liquidation_price: float,
        entry_or_sl: float,
        result: RiskCheckResult,
    ) -> None:
        """Gate 9: Liquidation price must be 30%+ away from entry."""
        if liquidation_price <= 0 or entry_or_sl <= 0:
            result.add_gate("liquidation_buffer", True, "N/A (paper mode)")
            return

        if direction == "LONG":
            distance = (entry_or_sl - liquidation_price) / entry_or_sl
        else:
            distance = (liquidation_price - entry_or_sl) / entry_or_sl

        min_buffer = self._risk("liquidation_buffer_pct")
        passed = distance >= min_buffer
        result.add_gate(
            "liquidation_buffer",
            passed,
            f"Liq distance: {distance:.1%} (min: {min_buffer:.1%})"
            if passed else
            f"Too close to liquidation: {distance:.1%} < {min_buffer:.1%}",
        )

    def _gate_funding_rate(
        self, funding_rate: float, result: RiskCheckResult
    ) -> None:
        """Gate 10: Funding rate check."""
        max_rate = self._risk("funding_rate_max")
        passed = abs(funding_rate) <= max_rate
        result.add_gate(
            "funding_rate",
            passed,
            f"Funding rate: {funding_rate:.4%} (max: {max_rate:.4%})"
            if passed else
            f"Excessive funding: {funding_rate:.4%} > {max_rate:.4%}",
        )
