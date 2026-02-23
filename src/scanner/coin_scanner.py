"""Coin scanner - discovers tradeable futures coins by volume and volatility."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from config.settings import RISK, SCANNER, MARKET
from src.clients.binance_rest import BinanceClient
from src.db.models import upsert_coin, has_position_for_symbol, was_recently_analyzed

logger = logging.getLogger(__name__)


@dataclass
class CoinCandidate:
    """A coin that passed scanner filters."""

    symbol: str
    base_asset: str
    volume_24h: float
    volatility_24h: float
    spread: float
    funding_rate: float
    last_price: float
    scan_score: float


async def scan_coins(
    client: BinanceClient,
    is_paper: bool = True,
    max_candidates: int | None = None,
) -> list[CoinCandidate]:
    """Scan Binance futures for tradeable coin candidates.

    Filters:
    1. USDT-M linear perpetual
    2. Minimum 24h volume ($50M)
    3. Minimum volatility (1.5%)
    4. Maximum spread
    5. Funding rate check
    6. No duplicate position
    7. Not excluded
    8. Not recently analyzed

    Scoring: volume * volatility * (1 - spread)
    """
    if max_candidates is None:
        max_candidates = SCANNER["max_candidates"]

    logger.info("Scanning coins (max %d candidates)...", max_candidates)

    tickers = await client.fetch_tickers()
    all_symbols = await client.get_futures_symbols()

    candidates: list[CoinCandidate] = []
    excluded = set(MARKET.get("exclude_symbols", []))

    for symbol in all_symbols:
        ticker = tickers.get(symbol)
        if not ticker:
            continue

        # Extract base asset
        base = symbol.split("/")[0] if "/" in symbol else symbol.replace("USDT", "")

        # Exclude list
        if symbol in excluded or base + "USDT" in excluded:
            continue

        # Filter 1: Volume
        volume_24h = float(ticker.get("quoteVolume", 0) or 0)
        if volume_24h < RISK["min_volume_24h"]:
            continue

        # Filter 2: Volatility (high - low) / close
        high = float(ticker.get("high", 0) or 0)
        low = float(ticker.get("low", 0) or 0)
        last = float(ticker.get("last", 0) or 0)
        if last <= 0:
            continue

        volatility = (high - low) / last if last > 0 else 0
        if volatility < RISK["min_volatility_pct"] / 100:
            continue

        # Filter 3: Spread
        bid = float(ticker.get("bid", 0) or 0)
        ask = float(ticker.get("ask", 0) or 0)
        spread = (ask - bid) / last if last > 0 and bid > 0 and ask > 0 else 0
        if spread > RISK["max_spread_pct"] / 100:
            continue

        # Filter 4: Funding rate
        funding_rate = 0.0
        try:
            fr = await client.fetch_funding_rate(symbol)
            funding_rate = float(fr.get("fundingRate", 0) or 0)
        except Exception:
            pass

        if abs(funding_rate) > RISK["funding_rate_max"]:
            continue

        # Filter 5: No duplicate position
        if await has_position_for_symbol(symbol, is_paper=is_paper):
            continue

        # Filter 6: Not recently analyzed
        cooldown = SCANNER.get("analysis_cooldown_hours", 4)
        if await was_recently_analyzed(symbol, cooldown_hours=cooldown):
            continue

        # Composite score
        score = volume_24h * volatility * (1 - spread)

        candidate = CoinCandidate(
            symbol=symbol,
            base_asset=base,
            volume_24h=volume_24h,
            volatility_24h=volatility,
            spread=spread,
            funding_rate=funding_rate,
            last_price=last,
            scan_score=score,
        )
        candidates.append(candidate)

        # Persist to DB
        await upsert_coin(
            symbol=symbol,
            base_asset=base,
            volume_24h=volume_24h,
            volatility_24h=volatility,
            spread=spread,
            funding_rate=funding_rate,
            scan_score=score,
        )

    # Sort by score descending
    candidates.sort(key=lambda c: c.scan_score, reverse=True)
    result = candidates[:max_candidates]

    logger.info(
        "Scan complete: %d/%d coins passed filters",
        len(result), len(all_symbols),
    )
    return result
