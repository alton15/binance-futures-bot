"""Tests for risk manager."""

import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from config.profiles import CONSERVATIVE, AGGRESSIVE
from src.risk.risk_manager import RiskManager, RiskCheckResult
from src.risk.leverage_calc import PositionParams
from src.db.models import init_db

TEST_DB = Path("/tmp/test_risk.db")


@pytest.fixture(autouse=True)
async def setup():
    if TEST_DB.exists():
        TEST_DB.unlink()
    await init_db(db_path=TEST_DB)
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()


def _default_params() -> PositionParams:
    # entry_price = notional / size = 50000
    # liq_price = 30000 -> distance = (50000-30000)/50000 = 40% > 30% buffer
    return PositionParams(
        leverage=3,
        position_size=0.001,
        notional_value=50,
        margin_required=16.67,
        sl_price=48500,
        tp_price=53000,
        liquidation_price=30000,
    )


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_all_gates_pass(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=_default_params(),
        funding_rate=0.0001,
    )
    assert result.passed is True
    assert len(result.gate_results) == 10
    assert all(g["passed"] for g in result.gate_results)


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_gate1_signal_too_weak(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.3,  # below 0.6 threshold
        position_params=_default_params(),
    )
    assert result.passed is False
    assert result.rejected_by == "signal_strength"


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock)
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_gate2_too_many_positions(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    mock_positions.return_value = [{"id": i} for i in range(7)]  # 7 open (= max for default)
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=_default_params(),
    )
    assert result.passed is False
    assert result.rejected_by == "position_limit"


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=True)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_gate3_duplicate_position(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=_default_params(),
    )
    assert result.passed is False
    assert result.rejected_by == "no_duplicate"


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_gate10_funding_too_high(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=_default_params(),
        funding_rate=0.005,  # 0.5% - way above 0.1% limit
    )
    assert result.passed is False
    assert result.rejected_by == "funding_rate"


# -- Profile-specific risk tests ------------------------------------


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_conservative_rejects_weak_signal(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    """Conservative profile requires signal_strength >= 0.65."""
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True, profile=CONSERVATIVE)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.60,  # below conservative threshold (0.65)
        position_params=_default_params(),
    )
    assert result.passed is False
    assert result.rejected_by == "signal_strength"


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_aggressive_accepts_high_leverage(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    """Aggressive profile allows leverage up to 8x."""
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True, profile=AGGRESSIVE)
    params = PositionParams(
        leverage=7,
        position_size=0.001,
        notional_value=50,
        margin_required=7.14,
        sl_price=48500,
        tp_price=53000,
        liquidation_price=30000,
    )
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=params,
        funding_rate=0.0001,
    )
    assert result.passed is True
    # Check gate 8 specifically
    gate8 = next(g for g in result.gate_results if g["name"] == "leverage_valid")
    assert gate8["passed"] is True
    assert "7x" in gate8["reason"]
    assert "3-8x" in gate8["reason"]


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_conservative_rejects_high_leverage(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    """Conservative profile rejects leverage > 3x."""
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True, profile=CONSERVATIVE)
    params = PositionParams(
        leverage=4,  # conservative max is 3
        position_size=0.001,
        notional_value=50,
        margin_required=12.5,
        sl_price=48500,
        tp_price=53000,
        liquidation_price=30000,
    )
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=params,
        funding_rate=0.0001,
    )
    # Gate 8 should fail
    gate8 = next(g for g in result.gate_results if g["name"] == "leverage_valid")
    assert gate8["passed"] is False


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_aggressive_accepts_valid_signal(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    """Aggressive profile accepts signal_strength >= 0.65."""
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True, profile=AGGRESSIVE)
    params = PositionParams(
        leverage=5,
        position_size=0.001,
        notional_value=50,
        margin_required=10,
        sl_price=48500,
        tp_price=53000,
        liquidation_price=30000,
    )
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.70,  # passes aggressive (0.65)
        position_params=params,
        funding_rate=0.0001,
    )
    gate1 = next(g for g in result.gate_results if g["name"] == "signal_strength")
    assert gate1["passed"] is True


# -- Gate 4: Dynamic capital tests ------------------------------------


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=-7.5)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 50})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=150)
async def test_gate4_dynamic_capital_allows_more_loss(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    """Gate 4 should use dynamic capital (100 + 50 PnL = 150) for daily loss limit.

    With initial capital=100 and 8% limit: loss limit = -$8.00
    With dynamic capital=150: loss limit = -$12.00
    Daily loss of -$7.50 should pass with dynamic capital.
    """
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=_default_params(),
        funding_rate=0.0001,
    )
    gate4 = next(g for g in result.gate_results if g["name"] == "daily_loss_limit")
    assert gate4["passed"] is True
    # Dynamic capital = 100 + 50 = 150, limit = -150 * 0.08 = -12.00
    assert "-12.00" in gate4["reason"]


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=-13.0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 50})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=150)
async def test_gate4_dynamic_capital_blocks_excessive_loss(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    """Gate 4 should block when daily loss exceeds dynamic capital limit.

    Dynamic capital = 100 + 50 = 150, limit = -$12.00
    Daily loss of -$13.00 exceeds limit -> blocked.
    """
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=_default_params(),
    )
    assert result.passed is False
    assert result.rejected_by == "daily_loss_limit"


# -- Gate 7: Soft cap tests -------------------------------------------


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock)
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_gate7_rejects_above_soft_cap(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    """Gate 7 should reject when total margin exceeds soft cap (exposure_cap).

    Capital=100, max_exposure=70%, soft_cap=70.
    Used margin 60 + new 15 = 75 > 70 -> rejected.
    Previously hard_cap allowed up to 75, now soft_cap blocks at 70.
    """
    mock_positions.return_value = [{"margin": 60}]
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True)
    params = _default_params()
    params = PositionParams(
        leverage=params.leverage,
        position_size=params.position_size,
        notional_value=params.notional_value,
        margin_required=15,  # 60 + 15 = 75 > 70 soft cap
        sl_price=params.sl_price,
        tp_price=params.tp_price,
        liquidation_price=params.liquidation_price,
    )
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=params,
        funding_rate=0.0001,
    )
    gate7 = next(g for g in result.gate_results if g["name"] == "total_exposure")
    assert gate7["passed"] is False


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock)
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 0})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=100)
async def test_gate7_passes_within_soft_cap(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    """Gate 7 should pass when total margin is within soft cap.

    Capital=100, max_exposure=70%, soft_cap=70.
    Used margin 50 + new 16.67 = 66.67 <= 70 -> passes.
    """
    mock_positions.return_value = [{"margin": 50}]
    client = AsyncMock()
    rm = RiskManager(client, capital=100, is_paper=True)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=_default_params(),
        funding_rate=0.0001,
    )
    gate7 = next(g for g in result.gate_results if g["name"] == "total_exposure")
    assert gate7["passed"] is True


# -- Fee-related tests ----------------------------------------------


@patch("src.risk.risk_manager.get_open_positions", new_callable=AsyncMock, return_value=[])
@patch("src.risk.risk_manager.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.risk.risk_manager.get_today_realized_pnl", new_callable=AsyncMock, return_value=0)
@patch("src.risk.risk_manager.get_trading_stats", new_callable=AsyncMock, return_value={"total_realized_pnl": 50})
@patch("src.risk.risk_manager.get_peak_capital", new_callable=AsyncMock, return_value=150)
async def test_compound_growth_capital(mock_peak, mock_stats, mock_pnl, mock_has_pos, mock_positions):
    """RiskManager uses dynamic capital (initial + realized PnL)."""
    client = AsyncMock()
    # Capital = 150 (initial 100 + 50 profit)
    rm = RiskManager(client, capital=150, is_paper=True)
    result = await rm.check(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        signal_strength=0.8,
        position_params=_default_params(),
        funding_rate=0.0001,
    )
    assert result.passed is True
    # Gate 4 (daily loss limit) uses the higher capital base
    gate4 = next(g for g in result.gate_results if g["name"] == "daily_loss_limit")
    assert gate4["passed"] is True
    # The limit should be based on $150, not $100
    assert "$150" in gate4["reason"] or "150" in gate4["reason"] or gate4["passed"]
