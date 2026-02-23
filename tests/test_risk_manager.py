"""Tests for risk manager."""

import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
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
    mock_positions.return_value = [{"id": i} for i in range(5)]  # 5 open
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
