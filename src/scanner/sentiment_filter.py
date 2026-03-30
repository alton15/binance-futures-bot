"""Sentiment filter - market-level fear & greed index for entry filtering.

Uses CoinGecko's Fear & Greed Index (free, no API key) to gauge
overall market sentiment and adjust signal strength accordingly.

Inspired by TradingAgents' Sentiment Analyst pattern, but rule-based.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# CoinGecko Fear & Greed endpoint (free, no key)
_FEAR_GREED_URL = "https://api.alternative.me/fng/"

# Cache duration (avoid hammering API)
_CACHE_TTL_SECONDS = 1800  # 30 minutes

# Thresholds
_EXTREME_FEAR = 20      # Index 0-20: extreme fear
_FEAR = 35              # Index 21-35: fear
_GREED = 65             # Index 65-79: greed
_EXTREME_GREED = 80     # Index 80-100: extreme greed

# Strength adjustments
_FEAR_LONG_BOOST = 0.05         # Fear → good for LONG (contrarian)
_GREED_SHORT_BOOST = 0.05       # Greed → good for SHORT (contrarian)
_EXTREME_FEAR_LONG_BOOST = 0.10
_EXTREME_GREED_SHORT_BOOST = 0.10
_GREED_LONG_PENALTY = 0.05      # Greed → risky for LONG (chasing)
_FEAR_SHORT_PENALTY = 0.05      # Fear → risky for SHORT (knife catching)
_EXTREME_GREED_LONG_PENALTY = 0.10
_EXTREME_FEAR_SHORT_PENALTY = 0.10


@dataclass(frozen=True)
class SentimentData:
    """Market sentiment data."""

    index: int          # 0-100 (0=extreme fear, 100=extreme greed)
    label: str          # "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
    timestamp: float    # Unix timestamp of data
    available: bool     # Whether data was fetched successfully


@dataclass(frozen=True)
class SentimentAdjustment:
    """Sentiment-based adjustment to signal strength."""

    adjustment: float   # Positive = boost, negative = penalty
    reason: str
    sentiment: SentimentData


# Module-level cache
_cached_sentiment: SentimentData | None = None
_cache_time: float = 0


async def fetch_sentiment() -> SentimentData:
    """Fetch current Fear & Greed Index.

    Caches result for 30 minutes to avoid rate limiting.
    Returns unavailable SentimentData on failure (never blocks trading).
    """
    global _cached_sentiment, _cache_time

    now = time.time()
    if _cached_sentiment is not None and (now - _cache_time) < _CACHE_TTL_SECONDS:
        return _cached_sentiment

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_FEAR_GREED_URL, params={"limit": 1})
            resp.raise_for_status()
            data = resp.json()

        entry = data.get("data", [{}])[0]
        index = int(entry.get("value", 50))
        label = entry.get("value_classification", "Neutral")
        timestamp = float(entry.get("timestamp", now))

        sentiment = SentimentData(
            index=index,
            label=label,
            timestamp=timestamp,
            available=True,
        )

        _cached_sentiment = sentiment
        _cache_time = now

        logger.info("Sentiment fetched: %s (%d)", label, index)
        return sentiment

    except Exception as e:
        logger.warning("Failed to fetch sentiment: %s (trading continues)", e)
        return SentimentData(
            index=50, label="Unavailable", timestamp=now, available=False,
        )


def evaluate_sentiment(
    direction: str,
    sentiment: SentimentData,
) -> SentimentAdjustment:
    """Evaluate how sentiment affects a signal's reliability.

    Contrarian logic:
    - Fear + LONG = boost (buy when others are fearful)
    - Greed + SHORT = boost (sell when others are greedy)
    - Greed + LONG = penalty (chasing the crowd)
    - Fear + SHORT = penalty (shorting into panic)
    """
    if not sentiment.available or direction == "NEUTRAL":
        return SentimentAdjustment(
            adjustment=0.0,
            reason="sentiment unavailable" if not sentiment.available else "neutral signal",
            sentiment=sentiment,
        )

    index = sentiment.index
    adjustment = 0.0
    reason = ""

    if direction == "LONG":
        if index <= _EXTREME_FEAR:
            adjustment = _EXTREME_FEAR_LONG_BOOST
            reason = f"extreme fear ({index}) boosts LONG (contrarian)"
        elif index <= _FEAR:
            adjustment = _FEAR_LONG_BOOST
            reason = f"fear ({index}) boosts LONG (contrarian)"
        elif index >= _EXTREME_GREED:
            adjustment = -_EXTREME_GREED_LONG_PENALTY
            reason = f"extreme greed ({index}) penalizes LONG (crowd chasing)"
        elif index >= _GREED:
            adjustment = -_GREED_LONG_PENALTY
            reason = f"greed ({index}) penalizes LONG (crowd chasing)"
        else:
            reason = f"neutral sentiment ({index})"

    elif direction == "SHORT":
        if index >= _EXTREME_GREED:
            adjustment = _EXTREME_GREED_SHORT_BOOST
            reason = f"extreme greed ({index}) boosts SHORT (contrarian)"
        elif index >= _GREED:
            adjustment = _GREED_SHORT_BOOST
            reason = f"greed ({index}) boosts SHORT (contrarian)"
        elif index <= _EXTREME_FEAR:
            adjustment = -_EXTREME_FEAR_SHORT_PENALTY
            reason = f"extreme fear ({index}) penalizes SHORT (panic selling)"
        elif index <= _FEAR:
            adjustment = -_FEAR_SHORT_PENALTY
            reason = f"fear ({index}) penalizes SHORT (panic selling)"
        else:
            reason = f"neutral sentiment ({index})"

    logger.info("Sentiment adjustment for %s: %+.2f%% (%s)", direction, adjustment * 100, reason)

    return SentimentAdjustment(
        adjustment=round(adjustment, 4),
        reason=reason,
        sentiment=sentiment,
    )


def reset_cache() -> None:
    """Reset sentiment cache (for testing)."""
    global _cached_sentiment, _cache_time
    _cached_sentiment = None
    _cache_time = 0
