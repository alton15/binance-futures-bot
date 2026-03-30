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
    """Zero ATR uses minimum SL distance (0.3% of price) instead of rejecting."""
    params = calculate_position(
        entry_price=50000,
        atr=0,
        direction="LONG",
        leverage=5,
    )
    # Min SL distance = 50000 * 0.003 = 150
    assert params.position_size > 0
    assert params.sl_price == 50000 - 150  # 49850


# -- Profile-specific leverage tests --------------------------------


def test_aggressive_max_leverage():
    """Aggressive profile: low volatility -> up to 8x."""
    lev = get_max_leverage(0.01, profile=AGGRESSIVE)
    assert lev == 8


def test_aggressive_calculate_leverage():
    """Aggressive profile: clamped to [3, 8]."""
    lev = calculate_leverage(
        volatility_24h=0.01,   # aggressive tier = 8x
        signal_strength=0.9,
        current_drawdown_pct=0,
        profile=AGGRESSIVE,
    )
    # 8 * 0.9 * 1.0 = 7.2 -> int(7.2) = 7
    assert lev == 7
    assert 3 <= lev <= 8


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


def test_swing_profiles_same_sl_multiplier():
    """All swing profiles use 2.0x ATR SL multiplier."""
    cons_params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=3, capital=100, profile=CONSERVATIVE,
    )
    neut_params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=3, capital=100, profile=NEUTRAL,
    )
    aggr_params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=3, capital=100, profile=AGGRESSIVE,
    )
    # All swing profiles: SL = 2.0 * 500 = 1000, price = 49000
    assert cons_params.sl_price == neut_params.sl_price == aggr_params.sl_price
    assert cons_params.sl_price == 49000.0


def test_scalp_wider_sl_than_swing():
    """Scalp SL multiplier (3.0x) is wider than swing (2.0x)."""
    scalp_params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=10, capital=100, profile=SCALP,
    )
    neut_params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=10, capital=100, profile=NEUTRAL,
    )
    # Scalp SL = 3.0 * 500 = 1500, price = 48500
    # Neutral SL = 2.0 * 500 = 1000, price = 49000
    assert scalp_params.sl_price < neut_params.sl_price


# -- Fee-adjusted position sizing tests -----------------------------


def test_fee_adjusted_position_sizing():
    """Position size should be smaller than raw risk/SL due to fee inclusion."""
    capital = 1000
    entry_price = 50000
    atr = 500
    sl_mult = 2.0
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

    sl_distance = atr * 2.0  # default SL multiplier (updated)
    sl_loss = params.position_size * sl_distance
    fee_cost = params.position_size * entry_price * 2 * FEES["taker_rate"]
    total_risk = sl_loss + fee_cost
    risk_amount = capital * 0.03  # default risk_per_trade

    assert total_risk <= risk_amount * 1.001  # allow tiny float rounding


# -- SCALP profile leverage tests ------------------------------------


def test_scalp_max_leverage_low_volatility():
    """SCALP profile: low volatility -> max 10x."""
    lev = get_max_leverage(0.01, profile=SCALP)
    assert lev == 10


def test_scalp_max_leverage_mid_volatility():
    """SCALP profile: mid volatility -> max 8x."""
    lev = get_max_leverage(0.03, profile=SCALP)
    assert lev == 8


def test_scalp_max_leverage_high_volatility():
    """SCALP profile: high volatility -> max 6x."""
    lev = get_max_leverage(0.05, profile=SCALP)
    assert lev == 6


def test_scalp_max_leverage_extreme_volatility():
    """SCALP profile: extreme volatility -> max 5x."""
    lev = get_max_leverage(0.10, profile=SCALP)
    assert lev == 5


def test_scalp_calculate_leverage_clamped():
    """SCALP profile: leverage clamped to [5, 10]."""
    lev = calculate_leverage(
        volatility_24h=0.01,   # scalp tier = 10x
        signal_strength=0.9,
        current_drawdown_pct=0,
        profile=SCALP,
    )
    # 10 * 0.9 * 1.0 = 9
    assert lev == 9
    assert 5 <= lev <= 10


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
    """SCALP profile: 0.8% risk with 3.0x ATR SL."""
    params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=10, capital=100, profile=SCALP,
    )
    assert params.leverage == 10
    assert params.position_size > 0
    # SL = 50000 - 3.0 * 500 = 48500
    assert params.sl_price == round(50000 - 3.0 * 500, 4)
    # TP = 50000 + 3.5 * 500 = 51750
    assert params.tp_price == round(50000 + 3.5 * 500, 4)


def test_scalp_margin_cap():
    """SCALP profile: margin capped at 10% of capital."""
    params = calculate_position(
        entry_price=50000, atr=50, direction="LONG",
        leverage=5, capital=100, profile=SCALP,
    )
    # max_margin_per_trade_pct = 0.10 -> max $10 margin
    assert params.margin_required <= 100 * 0.10 + 0.01  # float tolerance


# -- Market Precision Tests -----------------------------------------

from src.risk.leverage_calc import MarketPrecision


def test_precision_doge_integer_quantity():
    """DOGE requires integer quantity (0 decimal places)."""
    precision = MarketPrecision(amount_precision=0, price_precision=5)
    params = calculate_position(
        entry_price=0.175, atr=0.005, direction="LONG",
        leverage=5, capital=100, profile=NEUTRAL, precision=precision,
    )
    # Position size must be integer
    assert params.position_size == int(params.position_size)
    assert params.position_size > 0


def test_precision_btc_3dp_quantity():
    """BTC requires 3 decimal places for quantity."""
    precision = MarketPrecision(amount_precision=3, price_precision=1)
    params = calculate_position(
        entry_price=84000, atr=500, direction="LONG",
        leverage=3, capital=100, profile=NEUTRAL, precision=precision,
    )
    # Check no more than 3 decimals
    size_str = f"{params.position_size:.10f}"
    decimal_part = size_str.split(".")[1]
    assert all(c == "0" for c in decimal_part[3:])


def test_precision_sol_1dp_quantity():
    """SOL requires 1 decimal place for quantity."""
    precision = MarketPrecision(amount_precision=1, price_precision=2)
    params = calculate_position(
        entry_price=130.5, atr=3.0, direction="SHORT",
        leverage=4, capital=100, profile=NEUTRAL, precision=precision,
    )
    size_str = f"{params.position_size:.10f}"
    decimal_part = size_str.split(".")[1]
    assert all(c == "0" for c in decimal_part[1:])


def test_precision_shib_integer_with_large_qty():
    """SHIB: large integer quantity, no decimals."""
    precision = MarketPrecision(amount_precision=0, price_precision=8)
    params = calculate_position(
        entry_price=0.00001234, atr=0.0000002, direction="LONG",
        leverage=5, capital=100, profile=NEUTRAL, precision=precision,
    )
    assert params.position_size == int(params.position_size)
    assert params.position_size > 0


def test_precision_default_fallback():
    """Without precision, falls back to 6dp amount."""
    params = calculate_position(
        entry_price=50000, atr=500, direction="LONG",
        leverage=3, capital=100, profile=NEUTRAL,
    )
    # Default: 6 decimal places (old behavior)
    assert params.position_size > 0


def test_precision_price_from_exchange():
    """SL/TP uses exchange price precision when provided."""
    precision = MarketPrecision(amount_precision=3, price_precision=1)
    params = calculate_position(
        entry_price=84000, atr=500, direction="LONG",
        leverage=3, capital=100, profile=NEUTRAL, precision=precision,
    )
    # BTC price precision = 1dp
    sl_decimals = len(str(params.sl_price).rstrip("0").split(".")[-1]) if "." in str(params.sl_price) else 0
    assert sl_decimals <= 1


def test_precision_notional_recalculated():
    """Notional and margin should match rounded position size."""
    precision = MarketPrecision(amount_precision=0, price_precision=5)
    params = calculate_position(
        entry_price=0.175, atr=0.005, direction="LONG",
        leverage=5, capital=100, profile=NEUTRAL, precision=precision,
    )
    expected_notional = params.position_size * 0.175
    assert abs(params.notional_value - round(expected_notional, 4)) < 0.01


def test_market_precision_frozen():
    """MarketPrecision should be immutable."""
    prec = MarketPrecision(amount_precision=3, price_precision=1)
    with pytest.raises(AttributeError):
        prec.amount_precision = 5  # type: ignore[misc]
