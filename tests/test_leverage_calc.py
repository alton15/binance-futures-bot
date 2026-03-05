"""Tests for leverage calculator."""

import pytest
from config.profiles import CONSERVATIVE, NEUTRAL, AGGRESSIVE, SCALP
from config.settings import FEES
from src.risk.leverage_calc import (
    get_max_leverage,
    calculate_leverage,
    calculate_position,
    PositionParams,
)


def test_max_leverage_low_volatility():
    assert get_max_leverage(0.01) == 8   # 1% -> 8x


def test_max_leverage_mid_volatility():
    assert get_max_leverage(0.03) == 5   # 3% -> 5x


def test_max_leverage_high_volatility():
    assert get_max_leverage(0.05) == 3   # 5% -> 3x


def test_max_leverage_extreme_volatility():
    assert get_max_leverage(0.10) == 2   # 10% -> 2x


def test_calculate_leverage_strong_signal():
    lev = calculate_leverage(
        volatility_24h=0.03,   # tier = 5x
        signal_strength=0.9,
        current_drawdown_pct=0,
    )
    # 5 * 0.9 * 1.0 = 4.5 -> int(4.5) = 4
    assert lev == 4


def test_calculate_leverage_with_drawdown():
    lev = calculate_leverage(
        volatility_24h=0.01,   # tier = 8x
        signal_strength=0.8,
        current_drawdown_pct=0.10,
    )
    # 8 * 0.8 * 0.9 = 5.76 -> 5
    assert lev == 5


def test_calculate_leverage_clamp_min():
    lev = calculate_leverage(
        volatility_24h=0.10,
        signal_strength=0.3,
        current_drawdown_pct=0.5,
    )
    # 2 * 0.3 * 0.5 = 0.3 -> clamped to 2
    assert lev == 2


def test_calculate_position_long():
    params = calculate_position(
        entry_price=50000,
        atr=500,
        direction="LONG",
        leverage=5,
        capital=100,
    )
    assert isinstance(params, PositionParams)
    assert params.leverage == 5
    assert params.position_size > 0
    assert params.sl_price < 50000   # SL below entry for LONG
    assert params.tp_price > 50000   # TP above entry for LONG
    assert params.liquidation_price < 50000


def test_calculate_position_short():
    params = calculate_position(
        entry_price=50000,
        atr=500,
        direction="SHORT",
        leverage=5,
        capital=100,
    )
    assert params.sl_price > 50000   # SL above entry for SHORT
    assert params.tp_price < 50000   # TP below entry for SHORT
    assert params.liquidation_price > 50000


def test_calculate_position_margin():
    params = calculate_position(
        entry_price=50000,
        atr=500,
        direction="LONG",
        leverage=5,
        capital=100,
    )
    # margin = notional / leverage
    expected_margin = params.notional_value / 5
    assert abs(params.margin_required - expected_margin) < 0.01


def test_calculate_position_zero_atr():
    params = calculate_position(
        entry_price=50000,
        atr=0,
        direction="LONG",
        leverage=5,
    )
    assert params.position_size == 0


# -- Profile-specific leverage tests --------------------------------


def test_aggressive_max_leverage():
    """Aggressive profile: low volatility -> up to 10x."""
    lev = get_max_leverage(0.01, profile=AGGRESSIVE)
    assert lev == 10


def test_aggressive_calculate_leverage():
    """Aggressive profile: clamped to [3, 10]."""
    lev = calculate_leverage(
        volatility_24h=0.01,   # aggressive tier = 10x
        signal_strength=0.9,
        current_drawdown_pct=0,
        profile=AGGRESSIVE,
    )
    # 10 * 0.9 * 1.0 = 9
    assert lev == 9
    assert 3 <= lev <= 10


def test_conservative_max_leverage():
    """Conservative profile: low volatility -> max 3x."""
    lev = get_max_leverage(0.01, profile=CONSERVATIVE)
    assert lev == 3


def test_conservative_calculate_leverage():
    """Conservative profile: clamped to [1, 3]."""
    lev = calculate_leverage(
        volatility_24h=0.01,   # conservative tier = 3x
        signal_strength=0.9,
        current_drawdown_pct=0,
        profile=CONSERVATIVE,
    )
    # 3 * 0.9 * 1.0 = 2.7 -> int(2.7) = 2
    assert lev == 2
    assert 1 <= lev <= 3


def test_conservative_leverage_floor():
    """Conservative profile: minimum leverage is 1 (not 2)."""
    lev = calculate_leverage(
        volatility_24h=0.10,   # conservative tier = 1x
        signal_strength=0.3,
        current_drawdown_pct=0.5,
        profile=CONSERVATIVE,
    )
    # 1 * 0.3 * 0.5 = 0.15 -> clamped to 1
    assert lev == 1


def test_conservative_position_wider_sl():
    """Conservative SL multiplier (2.0) vs neutral (1.5): wider SL."""
    cons_params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=3, capital=100, profile=CONSERVATIVE,
    )
    neut_params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=3, capital=100, profile=NEUTRAL,
    )
    # Conservative SL is farther from entry (wider stop)
    assert cons_params.sl_price < neut_params.sl_price


def test_aggressive_same_sl_as_neutral():
    """Aggressive SL multiplier now equals neutral (1.5 ATR)."""
    aggr_params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=5, capital=100, profile=AGGRESSIVE,
    )
    neut_params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=5, capital=100, profile=NEUTRAL,
    )
    # Same SL multiplier (1.5) -> same SL price
    assert aggr_params.sl_price == neut_params.sl_price


# -- Fee-adjusted position sizing tests -----------------------------


def test_fee_adjusted_position_sizing():
    """Position size should be smaller than raw risk/SL due to fee inclusion."""
    capital = 1000
    entry_price = 50000
    atr = 500
    sl_mult = 1.5
    risk_pct = 0.03

    params = calculate_position(
        entry_price=entry_price,
        atr=atr,
        direction="LONG",
        leverage=5,
        capital=capital,
    )

    risk_amount = capital * risk_pct
    sl_distance = atr * sl_mult
    raw_position_size = risk_amount / sl_distance

    # Fee-adjusted size should be strictly less than raw
    assert params.position_size < raw_position_size


def test_maint_margin_from_settings():
    """Liquidation price should use maint_margin_rate from RISK settings."""
    from config.settings import RISK
    params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=5, capital=100,
    )
    # Verify liquidation formula: entry * (1 - 1/lev + maint_margin_rate)
    expected_liq = 50000 * (1 - 1/5 + RISK["maint_margin_rate"])
    assert abs(params.liquidation_price - round(expected_liq, 4)) < 0.01


def test_maint_margin_profile_override():
    """Profile can override maint_margin_rate for different liquidation price."""
    from config.profiles import ProfileConfig
    custom = ProfileConfig(
        name="custom", label="Custom",
        risk={"maint_margin_rate": 0.01},  # 1% instead of 0.5%
        leverage_min=2, leverage_max=8,
    )
    params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=5, capital=100, profile=custom,
    )
    expected_liq = 50000 * (1 - 1/5 + 0.01)
    assert abs(params.liquidation_price - round(expected_liq, 4)) < 0.01


def test_fee_cost_is_deducted_from_risk():
    """Verify fee is included in risk budget: SL loss + fees <= risk_amount."""
    capital = 1000
    entry_price = 50000
    atr = 500

    params = calculate_position(
        entry_price=entry_price,
        atr=atr,
        direction="LONG",
        leverage=5,
        capital=capital,
    )

    sl_distance = atr * 1.5  # default neutral SL multiplier
    sl_loss = params.position_size * sl_distance
    fee_cost = params.position_size * entry_price * 2 * FEES["taker_rate"]
    total_risk = sl_loss + fee_cost
    risk_amount = capital * 0.03  # default neutral risk_per_trade

    assert total_risk <= risk_amount * 1.001  # allow tiny float rounding


# -- SCALP profile leverage tests ------------------------------------


def test_scalp_max_leverage_low_volatility():
    """SCALP profile: low volatility -> max 15x."""
    lev = get_max_leverage(0.01, profile=SCALP)
    assert lev == 15


def test_scalp_max_leverage_mid_volatility():
    """SCALP profile: mid volatility -> max 12x."""
    lev = get_max_leverage(0.03, profile=SCALP)
    assert lev == 12


def test_scalp_max_leverage_high_volatility():
    """SCALP profile: high volatility -> max 8x."""
    lev = get_max_leverage(0.05, profile=SCALP)
    assert lev == 8


def test_scalp_max_leverage_extreme_volatility():
    """SCALP profile: extreme volatility -> max 5x."""
    lev = get_max_leverage(0.10, profile=SCALP)
    assert lev == 5


def test_scalp_calculate_leverage_clamped():
    """SCALP profile: leverage clamped to [5, 15]."""
    lev = calculate_leverage(
        volatility_24h=0.01,   # scalp tier = 15x
        signal_strength=0.9,
        current_drawdown_pct=0,
        profile=SCALP,
    )
    # 15 * 0.9 * 1.0 = 13.5 -> int(13.5) = 13
    assert lev == 13
    assert 5 <= lev <= 15


def test_scalp_leverage_floor():
    """SCALP profile: minimum leverage is 5."""
    lev = calculate_leverage(
        volatility_24h=0.10,   # scalp tier = 5x
        signal_strength=0.3,
        current_drawdown_pct=0.5,
        profile=SCALP,
    )
    # 5 * 0.3 * 0.5 = 0.75 -> clamped to 5
    assert lev == 5


def test_scalp_position_sizing():
    """SCALP profile: 1% risk with 1.5x ATR SL."""
    params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=10, capital=100, profile=SCALP,
    )
    assert params.leverage == 10
    assert params.position_size > 0
    # SL = 50000 - 1.5 * 500 = 49250
    assert params.sl_price == round(50000 - 1.5 * 500, 4)
    # TP = 50000 + 3.0 * 500 = 51500
    assert params.tp_price == round(50000 + 3.0 * 500, 4)


def test_scalp_margin_cap():
    """SCALP profile: margin capped at 10% of capital."""
    params = calculate_position(
        entry_price=50000, atr=50, direction="LONG",
        leverage=5, capital=100, profile=SCALP,
    )
    # max_margin_per_trade_pct = 0.10 -> max $10 margin
    assert params.margin_required <= 100 * 0.10 + 0.01  # float tolerance
