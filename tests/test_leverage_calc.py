"""Tests for leverage calculator."""

import pytest
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
