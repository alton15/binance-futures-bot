"""Tests for database models."""

import pytest
from pathlib import Path
from src.db.models import (
    init_db,
    upsert_coin,
    get_coin,
    save_signal,
    was_recently_analyzed,
    save_trade,
    update_trade_status,
    open_position,
    close_position,
    get_open_positions,
    update_position_price,
    has_position_for_symbol,
    save_pnl_snapshot,
    get_today_realized_pnl,
    get_trading_stats,
    get_risk_summary,
    save_funding_payment,
    save_indicator_snapshot,
)

TEST_DB = Path("/tmp/test_futures_bot.db")


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize a fresh test DB for each test."""
    if TEST_DB.exists():
        TEST_DB.unlink()
    await init_db(db_path=TEST_DB)
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()


async def test_init_db():
    """Tables should be created."""
    import aiosqlite
    async with aiosqlite.connect(str(TEST_DB)) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]

    expected = [
        "coins", "funding_payments", "indicator_snapshots",
        "orders", "pnl_snapshots", "positions", "signals", "trades",
    ]
    for t in expected:
        assert t in tables, f"Missing table: {t}"


async def test_upsert_and_get_coin():
    await upsert_coin(
        symbol="BTC/USDT:USDT",
        base_asset="BTC",
        volume_24h=1_000_000_000,
        volatility_24h=0.03,
        spread=0.0001,
        funding_rate=0.0001,
        scan_score=0.85,
        db_path=TEST_DB,
    )
    coin = await get_coin("BTC/USDT:USDT", db_path=TEST_DB)
    assert coin is not None
    assert coin["symbol"] == "BTC/USDT:USDT"
    assert coin["volume_24h"] == 1_000_000_000

    # Upsert should update
    await upsert_coin(
        symbol="BTC/USDT:USDT", volume_24h=2_000_000_000, db_path=TEST_DB
    )
    coin = await get_coin("BTC/USDT:USDT", db_path=TEST_DB)
    assert coin["volume_24h"] == 2_000_000_000


async def test_save_signal():
    sig_id = await save_signal(
        symbol="ETH/USDT:USDT",
        direction="LONG",
        strength=0.75,
        confirming_count=4,
        db_path=TEST_DB,
    )
    assert sig_id > 0


async def test_recently_analyzed():
    result = await was_recently_analyzed("ETH/USDT:USDT", db_path=TEST_DB)
    assert result is False

    await save_signal(
        symbol="ETH/USDT:USDT", direction="LONG", strength=0.7, db_path=TEST_DB
    )
    result = await was_recently_analyzed("ETH/USDT:USDT", db_path=TEST_DB)
    assert result is True


async def test_trade_lifecycle():
    trade_id = await save_trade(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        entry_price=50000,
        size=0.01,
        cost=500,
        leverage=5,
        margin=100,
        order_id="test-001",
        status="pending",
        db_path=TEST_DB,
    )
    assert trade_id > 0

    await update_trade_status(
        trade_id, "filled", fill_price=50010, fill_size=0.01, db_path=TEST_DB
    )


async def test_position_lifecycle():
    pos_id = await open_position(
        symbol="BTC/USDT:USDT",
        direction="LONG",
        entry_price=50000,
        size=0.01,
        cost=500,
        leverage=5,
        margin=100,
        liquidation_price=42000,
        sl_price=48500,
        tp_price=53000,
        db_path=TEST_DB,
    )
    assert pos_id > 0

    positions = await get_open_positions(is_paper=True, db_path=TEST_DB)
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTC/USDT:USDT"
    assert positions[0]["leverage"] == 5

    has_pos = await has_position_for_symbol("BTC/USDT:USDT", db_path=TEST_DB)
    assert has_pos is True

    await update_position_price(
        pos_id, current_price=51000, unrealized_pnl=10, db_path=TEST_DB
    )

    await close_position(pos_id, realized_pnl=10, exit_reason="take_profit", db_path=TEST_DB)

    positions = await get_open_positions(is_paper=True, db_path=TEST_DB)
    assert len(positions) == 0


async def test_trading_stats_empty():
    stats = await get_trading_stats(db_path=TEST_DB)
    assert stats["total_trades"] == 0
    assert stats["win_rate"] == 0


async def test_risk_summary_empty():
    summary = await get_risk_summary(db_path=TEST_DB)
    assert summary["total_margin"] == 0
    assert summary["open_positions"] == 0


async def test_pnl_snapshot():
    await save_pnl_snapshot(
        total_capital=100, daily_pnl=5, peak_capital=100, db_path=TEST_DB
    )


async def test_funding_payment():
    pos_id = await open_position(
        symbol="ETH/USDT:USDT", direction="LONG",
        entry_price=3000, size=0.1, cost=300, db_path=TEST_DB,
    )
    await save_funding_payment(
        symbol="ETH/USDT:USDT", position_id=pos_id,
        funding_rate=0.0001, payment=-0.03, db_path=TEST_DB,
    )


async def test_indicator_snapshot():
    await save_indicator_snapshot(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        close_price=50000,
        rsi=55,
        macd=100,
        atr=500,
        db_path=TEST_DB,
    )
