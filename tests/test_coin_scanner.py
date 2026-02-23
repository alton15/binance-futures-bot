"""Tests for coin scanner (unit tests with mocked client)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from src.scanner.coin_scanner import scan_coins, CoinCandidate
from src.db.models import init_db

TEST_DB = Path("/tmp/test_scanner.db")


def _mock_ticker(
    symbol: str,
    quote_volume: float = 100_000_000,
    high: float = 51000,
    low: float = 49000,
    last: float = 50000,
    bid: float = 49999,
    ask: float = 50001,
) -> dict:
    return {
        "symbol": symbol,
        "quoteVolume": quote_volume,
        "high": high,
        "low": low,
        "last": last,
        "bid": bid,
        "ask": ask,
    }


@pytest.fixture(autouse=True)
async def setup():
    if TEST_DB.exists():
        TEST_DB.unlink()
    await init_db(db_path=TEST_DB)
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()


@patch("src.scanner.coin_scanner.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.scanner.coin_scanner.was_recently_analyzed", new_callable=AsyncMock, return_value=False)
@patch("src.scanner.coin_scanner.upsert_coin", new_callable=AsyncMock)
async def test_scan_returns_candidates(mock_upsert, mock_analyzed, mock_has_pos):
    client = AsyncMock()
    client.get_futures_symbols.return_value = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    client.fetch_tickers.return_value = {
        "BTC/USDT:USDT": _mock_ticker("BTC/USDT:USDT"),
        "ETH/USDT:USDT": _mock_ticker("ETH/USDT:USDT", quote_volume=80_000_000,
                                        high=3200, low=3000, last=3100, bid=3099.9, ask=3100.1),
    }
    client.fetch_funding_rate.return_value = {"fundingRate": 0.0001}

    candidates = await scan_coins(client, max_candidates=5)
    assert len(candidates) == 2
    assert all(isinstance(c, CoinCandidate) for c in candidates)


@patch("src.scanner.coin_scanner.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.scanner.coin_scanner.was_recently_analyzed", new_callable=AsyncMock, return_value=False)
@patch("src.scanner.coin_scanner.upsert_coin", new_callable=AsyncMock)
async def test_scan_filters_low_volume(mock_upsert, mock_analyzed, mock_has_pos):
    client = AsyncMock()
    client.get_futures_symbols.return_value = ["LOW/USDT:USDT"]
    client.fetch_tickers.return_value = {
        "LOW/USDT:USDT": _mock_ticker("LOW/USDT:USDT", quote_volume=1_000_000),
    }
    client.fetch_funding_rate.return_value = {"fundingRate": 0.0001}

    candidates = await scan_coins(client, max_candidates=5)
    assert len(candidates) == 0


@patch("src.scanner.coin_scanner.has_position_for_symbol", new_callable=AsyncMock, return_value=False)
@patch("src.scanner.coin_scanner.was_recently_analyzed", new_callable=AsyncMock, return_value=False)
@patch("src.scanner.coin_scanner.upsert_coin", new_callable=AsyncMock)
async def test_scan_filters_low_volatility(mock_upsert, mock_analyzed, mock_has_pos):
    client = AsyncMock()
    client.get_futures_symbols.return_value = ["FLAT/USDT:USDT"]
    client.fetch_tickers.return_value = {
        "FLAT/USDT:USDT": _mock_ticker(
            "FLAT/USDT:USDT",
            high=50010, low=49990, last=50000,  # 0.04% volatility
        ),
    }
    client.fetch_funding_rate.return_value = {"fundingRate": 0.0001}

    candidates = await scan_coins(client, max_candidates=5)
    assert len(candidates) == 0


@patch("src.scanner.coin_scanner.has_position_for_symbol", new_callable=AsyncMock, return_value=True)
@patch("src.scanner.coin_scanner.was_recently_analyzed", new_callable=AsyncMock, return_value=False)
@patch("src.scanner.coin_scanner.upsert_coin", new_callable=AsyncMock)
async def test_scan_filters_existing_position(mock_upsert, mock_analyzed, mock_has_pos):
    client = AsyncMock()
    client.get_futures_symbols.return_value = ["BTC/USDT:USDT"]
    client.fetch_tickers.return_value = {
        "BTC/USDT:USDT": _mock_ticker("BTC/USDT:USDT"),
    }
    client.fetch_funding_rate.return_value = {"fundingRate": 0.0001}

    candidates = await scan_coins(client, max_candidates=5)
    assert len(candidates) == 0
