"""Fee-aware, risk-based position sizing with leverage and liquidation checks."""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.scalper.config import MAKER_FEE, TAKER_FEE, RiskConfig

STOP_SLIPPAGE = 0.0002          # assumed adverse slippage on stop exits
MAINT_MARGIN_RATE = 0.005       # conservative lowest-tier MMR for liquidation estimate


@dataclass(frozen=True)
class SizeResult:
    qty: float
    notional: float
    leverage: int
    margin: float
    risk_usdt: float
    reason: str = ""            # non-empty when sizing was rejected

    @property
    def ok(self) -> bool:
        return self.qty > 0 and not self.reason


def _floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step + 1e-9) * step


def _reject(reason: str) -> SizeResult:
    return SizeResult(0.0, 0.0, 0, 0.0, 0.0, reason)


def size_position(
    equity: float,
    entry: float,
    sl: float,
    maker_entry: bool,
    cfg: RiskConfig,
    qty_step: float,
    min_notional: float,
) -> SizeResult:
    """Size so that hitting the stop (plus fees/slippage) loses equity*risk_per_trade."""
    if equity <= 0 or entry <= 0 or sl <= 0:
        return _reject("invalid inputs")
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return _reject("zero stop distance")
    fee_in = MAKER_FEE if maker_entry else TAKER_FEE
    loss_per_unit = sl_dist + entry * fee_in + sl * (TAKER_FEE + STOP_SLIPPAGE)
    risk_budget = equity * cfg.risk_per_trade
    qty = risk_budget / loss_per_unit

    # Leverage: each position's margin must fit within an equal slice of equity.
    margin_slice = equity / cfg.max_positions
    max_notional = min(margin_slice * cfg.max_leverage, equity * cfg.max_notional_frac)
    qty = min(qty, max_notional / entry)

    # Liquidation must be much farther than the stop.
    sl_frac = sl_dist / entry
    max_lev_for_liq = int(1.0 / (cfg.liq_buffer * sl_frac + MAINT_MARGIN_RATE))
    lev_cap = max(1, min(cfg.max_leverage, max_lev_for_liq))
    qty = min(qty, margin_slice * lev_cap / entry)

    qty = _floor_step(qty, qty_step)
    notional = qty * entry
    if notional < max(min_notional, cfg.min_notional_usdt):
        return _reject(f"notional {notional:.2f} below minimum")
    leverage = max(1, min(lev_cap, math.ceil(notional / margin_slice)))
    return SizeResult(
        qty=qty,
        notional=notional,
        leverage=leverage,
        margin=notional / leverage,
        risk_usdt=qty * loss_per_unit,
    )
