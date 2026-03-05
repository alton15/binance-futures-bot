"""Tests for live order executor P&L calculation."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from config.settings import FEES
from src.trading.order_executor import OrderExecutor


@pytest.fixture
def executor():
    client = AsyncMock()
    return OrderExecutor(client)


@patch("src.trading.order_executor.close_position", new_callable=AsyncMock)
async def test_close_order_deducts_fees(mock_close, executor):
    """close_order should deduct round-trip fees from P&L."""
    executor.client.cancel_all_orders = AsyncMock()
    executor.client.close_position = AsyncMock(return_value={
        "average": 51000,
        "id": "test-order-123",
    })

    position = {
        "id": 1,
        "symbol": "BTC/USDT:USDT",
        "direction": "LONG",
        "entry_price": 50000,
        "size": 0.01,
        "funding_paid": 0,
    }

    result = await executor.close_order(position, 51000, "take_profit")
    assert result["success"] is True

    # Calculate expected fee deduction
    entry_notional = 50000 * 0.01  # 500
    exit_notional = 51000 * 0.01   # 510
    fee_rate = FEES["taker_rate"] + FEES["slippage_rate"]
    total_fees = (entry_notional + exit_notional) * fee_rate

    raw_pnl = (51000 - 50000) * 0.01  # 10
    expected_net = raw_pnl - total_fees
    assert abs(result["pnl"] - expected_net) < 0.0001


@patch("src.trading.order_executor.close_position", new_callable=AsyncMock)
async def test_close_order_deducts_funding(mock_close, executor):
    """close_order should deduct funding_paid from P&L."""
    executor.client.cancel_all_orders = AsyncMock()
    executor.client.close_position = AsyncMock(return_value={
        "average": 51000,
        "id": "test-order-456",
    })

    position = {
        "id": 2,
        "symbol": "BTC/USDT:USDT",
        "direction": "LONG",
        "entry_price": 50000,
        "size": 0.01,
        "funding_paid": 0.5,
    }

    result = await executor.close_order(position, 51000, "manual")
    assert result["success"] is True

    entry_notional = 50000 * 0.01
    exit_notional = 51000 * 0.01
    fee_rate = FEES["taker_rate"] + FEES["slippage_rate"]
    total_fees = (entry_notional + exit_notional) * fee_rate

    raw_pnl = (51000 - 50000) * 0.01
    expected_net = raw_pnl - 0.5 - total_fees
    assert abs(result["pnl"] - expected_net) < 0.0001


@patch("src.trading.order_executor.close_position", new_callable=AsyncMock)
async def test_close_order_short_pnl_with_fees(mock_close, executor):
    """SHORT P&L should also deduct fees."""
    executor.client.cancel_all_orders = AsyncMock()
    executor.client.close_position = AsyncMock(return_value={
        "average": 49000,
        "id": "test-order-789",
    })

    position = {
        "id": 3,
        "symbol": "BTC/USDT:USDT",
        "direction": "SHORT",
        "entry_price": 50000,
        "size": 0.01,
        "funding_paid": 0,
    }

    result = await executor.close_order(position, 49000, "take_profit")
    assert result["success"] is True

    raw_pnl = (50000 - 49000) * 0.01  # 10
    entry_notional = 50000 * 0.01
    exit_notional = 49000 * 0.01
    fee_rate = FEES["taker_rate"] + FEES["slippage_rate"]
    total_fees = (entry_notional + exit_notional) * fee_rate

    expected_net = raw_pnl - total_fees
    assert abs(result["pnl"] - expected_net) < 0.0001
    assert result["pnl"] < raw_pnl  # net should be less than raw
