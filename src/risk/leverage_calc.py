"""Dynamic leverage and position sizing calculator."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from config.settings import RISK, LEVERAGE_TIERS, INITIAL_CAPITAL, FEES

if TYPE_CHECKING:
    from config.profiles import ProfileConfig

logger = logging.getLogger(__name__)


@dataclass
class PositionParams:
    """Calculated position parameters."""

    leverage: int
    position_size: float     # Quantity in base currency
    notional_value: float    # USD value of position
    margin_required: float   # USDT margin required
    sl_price: float
    tp_price: float
    liquidation_price: float


def _price_precision(price: float) -> int:
    """Determine rounding precision based on price magnitude.

    Low-priced coins need more decimals to preserve SL/TP distances.
    """
    if price <= 0:
        return 8
    # Number of decimals needed: e.g. $0.0002 → 8, $1.5 → 6, $50000 → 2
    magnitude = -math.floor(math.log10(price))
    return max(2, min(8, magnitude + 4))


def get_max_leverage(
    volatility_24h: float,
    profile: ProfileConfig | None = None,
) -> int:
    """Get max allowed leverage based on daily volatility tier.

    Uses profile-specific leverage tiers when provided.
    """
    tiers = profile.get_leverage_tiers() if profile else LEVERAGE_TIERS
    for tier in tiers:
        if volatility_24h <= tier["max_volatility"]:
            return tier["max_leverage"]
    return 2


def calculate_leverage(
    volatility_24h: float,
    signal_strength: float,
    current_drawdown_pct: float = 0,
    profile: ProfileConfig | None = None,
) -> int:
    """Calculate dynamic leverage.

    Formula: max_tier_leverage * signal_strength * (1 - drawdown_pct)
    Clamped to profile's [leverage_min, leverage_max] range.
    """
    max_lev = get_max_leverage(volatility_24h, profile=profile)
    raw = max_lev * signal_strength * (1 - current_drawdown_pct)

    lev_min = profile.leverage_min if profile else 2
    lev_max = profile.leverage_max if profile else 8
    return max(lev_min, min(lev_max, int(raw)))


def calculate_position(
    entry_price: float,
    atr: float,
    direction: str,
    leverage: int,
    capital: float = INITIAL_CAPITAL,
    volatility_24h: float = 0.03,
    profile: ProfileConfig | None = None,
) -> PositionParams:
    """Calculate full position parameters.

    Uses fixed-fraction risk model with profile-specific multipliers.
    """
    risk_pct = profile.get_risk("risk_per_trade_pct") if profile else RISK["risk_per_trade_pct"]
    sl_mult = profile.get_risk("sl_atr_multiplier") if profile else RISK["sl_atr_multiplier"]
    tp_mult = profile.get_risk("tp_atr_multiplier") if profile else RISK["tp_atr_multiplier"]

    risk_amount = capital * risk_pct
    sl_distance = atr * sl_mult
    tp_distance = atr * tp_mult

    # Minimum SL distance: at least 0.3% of entry price
    min_sl_distance = entry_price * 0.003
    if sl_distance < min_sl_distance:
        logger.info(
            "SL distance %.8f too small for price %.8f, using minimum %.8f",
            sl_distance, entry_price, min_sl_distance,
        )
        sl_distance = min_sl_distance
        tp_distance = max(tp_distance, min_sl_distance * (tp_mult / sl_mult))

    if sl_distance <= 0 or entry_price <= 0:
        logger.warning("Invalid SL distance or entry price")
        return PositionParams(
            leverage=leverage,
            position_size=0,
            notional_value=0,
            margin_required=0,
            sl_price=0,
            tp_price=0,
            liquidation_price=0,
        )

    # Position size in base currency (fee-adjusted)
    # Actual risk = SL loss + round-trip fees, so include fees in risk budget
    fee_cost_per_unit = entry_price * 2 * FEES["taker_rate"]
    position_size = risk_amount / (sl_distance + fee_cost_per_unit)
    notional_value = position_size * entry_price
    margin_required = notional_value / leverage

    # Cap margin per position (profile-configurable)
    margin_pct = profile.get_risk("max_margin_per_trade_pct") if profile else 0.15
    max_margin_per_trade = capital * margin_pct
    if margin_required > max_margin_per_trade:
        scale = max_margin_per_trade / margin_required
        position_size *= scale
        notional_value *= scale
        margin_required = max_margin_per_trade

    # SL/TP prices
    if direction == "LONG":
        sl_price = entry_price - sl_distance
        tp_price = entry_price + tp_distance
    else:  # SHORT
        sl_price = entry_price + sl_distance
        tp_price = entry_price - tp_distance

    # Liquidation price (simplified)
    maint_margin = profile.get_risk("maint_margin_rate") if profile else RISK["maint_margin_rate"]
    if direction == "LONG":
        liquidation_price = entry_price * (1 - (1 / leverage) + maint_margin)
    else:
        liquidation_price = entry_price * (1 + (1 / leverage) - maint_margin)

    # Dynamic precision: use enough decimals to preserve SL/TP distance
    price_precision = _price_precision(entry_price)

    params = PositionParams(
        leverage=leverage,
        position_size=round(position_size, 6),
        notional_value=round(notional_value, 4),
        margin_required=round(margin_required, 4),
        sl_price=round(sl_price, price_precision),
        tp_price=round(tp_price, price_precision),
        liquidation_price=round(liquidation_price, price_precision),
    )

    logger.info(
        "Position calc: %s %dx size=%.6f notional=$%.2f margin=$%.2f SL=%s TP=%s liq=%s",
        direction, leverage, params.position_size, params.notional_value,
        params.margin_required, params.sl_price, params.tp_price,
        params.liquidation_price,
    )

    return params
