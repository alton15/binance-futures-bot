"""Portfolio-level guard: kill switches, position limits, per-symbol locks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from src.scalper.config import RiskConfig

logger = logging.getLogger(__name__)


def _utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


class RiskGuard:
    """Tracks equity and exposure; decides whether new entries are allowed.

    State is mutated only through explicit methods so the engine has one
    place that owns portfolio truth.
    """

    def __init__(self, cfg: RiskConfig, equity: float, peak_equity: float | None = None,
                 now: float = 0.0, day_start_equity: float | None = None) -> None:
        """`peak_equity` / `day_start_equity` come from storage so kill switches
        survive restarts (a restart must not grant a fresh daily-loss budget)."""
        self._cfg = cfg
        self._equity = equity
        self._peak = max(peak_equity or equity, equity)
        self._day = _utc_day(now)
        self._day_start = day_start_equity if day_start_equity is not None else equity
        self._halted_reason = ""
        self._busy: set[str] = set()            # open or pending symbols
        self._cooldown_until: dict[str, float] = {}
        self._check_halts()

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def halted_reason(self) -> str:
        return self._halted_reason

    @property
    def open_count(self) -> int:
        return len(self._busy)

    def _roll_day(self, now: float) -> None:
        day = _utc_day(now)
        if day != self._day:
            self._day = day
            self._day_start = self._equity
            if self._halted_reason.startswith("daily"):
                logger.info("New UTC day: clearing daily loss halt")
                self._halted_reason = ""

    def can_enter(self, symbol: str, now: float) -> GuardDecision:
        self._roll_day(now)
        if self._halted_reason:
            return GuardDecision(False, self._halted_reason)
        if symbol in self._busy:
            return GuardDecision(False, "symbol busy")
        if now < self._cooldown_until.get(symbol, 0.0):
            return GuardDecision(False, "cooldown")
        if len(self._busy) >= self._cfg.max_positions:
            return GuardDecision(False, "max positions")
        return GuardDecision(True)

    def reserve(self, symbol: str) -> None:
        self._busy.add(symbol)

    def release(self, symbol: str, now: float, cooldown_s: float) -> None:
        self._busy.discard(symbol)
        self._cooldown_until[symbol] = now + cooldown_s

    def record_pnl(self, net_pnl: float, now: float) -> None:
        self._roll_day(now)
        self._equity += net_pnl
        self._peak = max(self._peak, self._equity)
        self._check_halts()

    def _check_halts(self) -> None:
        day_loss = (self._day_start - self._equity) / self._day_start if self._day_start > 0 else 0
        drawdown = (self._peak - self._equity) / self._peak if self._peak > 0 else 0
        if drawdown >= self._cfg.max_drawdown:
            self._halted_reason = f"max drawdown {drawdown:.1%} (manual reset required)"
        elif day_loss >= self._cfg.daily_loss_limit and not self._halted_reason:
            self._halted_reason = f"daily loss {day_loss:.1%}"
        if self._halted_reason:
            logger.warning("RiskGuard HALT: %s", self._halted_reason)

    def halt(self, reason: str) -> None:
        self._halted_reason = reason
