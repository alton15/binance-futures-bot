"""BM25-based situation memory - learns from past trading outcomes.

Inspired by TradingAgents' FinancialSituationMemory.
Stores past trading situations as text descriptions + outcomes,
then uses BM25 lexical similarity to find similar past situations
and inform new trading decisions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from rank_bm25 import BM25Okapi

from src.db.models import (
    DEFAULT_DB_PATH,
    save_situation_outcome,
    get_situation_outcomes,
)
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimum situations needed before memory influences decisions
_MIN_SITUATIONS = 5

# Win rate threshold - below this, reduce position size
_LOW_WIN_RATE_THRESHOLD = 0.40

# Position size reduction when similar situations had low win rate
_LOW_WIN_RATE_SCALE = 0.70  # 30% reduction

# Number of similar situations to retrieve
_TOP_K = 5


@dataclass(frozen=True)
class MemoryQuery:
    """Result from querying situation memory."""

    similar_count: int
    similar_win_rate: float
    should_reduce: bool
    scale_factor: float  # 1.0 = no change, 0.7 = 30% reduction
    similar_situations: tuple[dict[str, Any], ...] = ()


def build_situation_text(
    symbol: str,
    direction: str,
    strength: float,
    confirming_count: int,
    details: dict[str, Any],
    rsi: float | None = None,
    adx: float | None = None,
    atr: float | None = None,
) -> str:
    """Build a searchable text description of a trading situation.

    This text is what gets stored and searched via BM25.
    """
    parts = [
        f"symbol:{symbol}",
        f"direction:{direction}",
        f"strength:{strength:.2f}",
        f"confirming:{confirming_count}",
    ]

    if rsi is not None:
        if rsi < 30:
            parts.append("rsi:oversold")
        elif rsi > 70:
            parts.append("rsi:overbought")
        elif rsi < 40:
            parts.append("rsi:low")
        elif rsi > 60:
            parts.append("rsi:high")
        else:
            parts.append("rsi:neutral")

    if adx is not None:
        if adx >= 25:
            parts.append("trend:strong")
        elif adx >= 15:
            parts.append("trend:moderate")
        else:
            parts.append("trend:weak")

    # Add each indicator's vote direction and reason
    for indicator, vote in details.items():
        vote_dir = vote.get("direction", "NEUTRAL")
        reason = vote.get("reason", "")
        parts.append(f"{indicator}:{vote_dir}")
        # Extract key terms from reason
        for keyword in ("crossover", "oversold", "overbought", "divergence",
                        "expanding", "upper", "lower", "golden", "death"):
            if keyword in reason.lower():
                parts.append(keyword)

    return " ".join(parts)


async def record_situation(
    symbol: str,
    direction: str,
    strength: float,
    confirming_count: int,
    details: dict[str, Any],
    realized_pnl: float,
    exit_reason: str = "",
    profile: str = "neutral",
    rsi: float | None = None,
    adx: float | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Record a completed trade situation and its outcome."""
    situation_text = build_situation_text(
        symbol=symbol,
        direction=direction,
        strength=strength,
        confirming_count=confirming_count,
        details=details,
        rsi=rsi,
        adx=adx,
    )

    indicator_snapshot = json.dumps(details, default=str)
    is_win = 1 if realized_pnl > 0 else 0

    await save_situation_outcome(
        symbol=symbol,
        direction=direction,
        situation_text=situation_text,
        indicator_snapshot=indicator_snapshot,
        strength=strength,
        confirming_count=confirming_count,
        realized_pnl=realized_pnl,
        exit_reason=exit_reason,
        is_win=is_win,
        profile=profile,
        db_path=db_path,
    )

    logger.info(
        "Recorded situation: %s %s pnl=%.4f win=%s",
        symbol, direction, realized_pnl, is_win,
    )


async def query_similar_situations(
    symbol: str,
    direction: str,
    strength: float,
    confirming_count: int,
    details: dict[str, Any],
    profile: str = "neutral",
    rsi: float | None = None,
    adx: float | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> MemoryQuery:
    """Query memory for similar past situations and their outcomes.

    Uses BM25 to find similar trading situations and calculates
    the win rate of those situations.
    """
    situations = await get_situation_outcomes(profile=profile, db_path=db_path)

    if len(situations) < _MIN_SITUATIONS:
        return MemoryQuery(
            similar_count=0,
            similar_win_rate=0.0,
            should_reduce=False,
            scale_factor=1.0,
        )

    # Build BM25 index from stored situations
    corpus = [s["situation_text"].split() for s in situations]
    bm25 = BM25Okapi(corpus)

    # Build query from current situation
    query_text = build_situation_text(
        symbol=symbol,
        direction=direction,
        strength=strength,
        confirming_count=confirming_count,
        details=details,
        rsi=rsi,
        adx=adx,
    )
    query_tokens = query_text.split()

    # Get top-K similar situations
    scores = bm25.get_scores(query_tokens)
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:_TOP_K]

    similar = [situations[i] for i in top_indices if scores[i] > 0]

    if not similar:
        return MemoryQuery(
            similar_count=0,
            similar_win_rate=0.0,
            should_reduce=False,
            scale_factor=1.0,
        )

    wins = sum(1 for s in similar if s.get("is_win", 0) == 1)
    win_rate = wins / len(similar) if similar else 0.0

    should_reduce = win_rate < _LOW_WIN_RATE_THRESHOLD
    scale_factor = _LOW_WIN_RATE_SCALE if should_reduce else 1.0

    logger.info(
        "Memory query %s %s: %d similar situations, win_rate=%.1f%%, reduce=%s",
        symbol, direction, len(similar), win_rate * 100, should_reduce,
    )

    return MemoryQuery(
        similar_count=len(similar),
        similar_win_rate=round(win_rate, 4),
        should_reduce=should_reduce,
        scale_factor=scale_factor,
        similar_situations=tuple(similar),
    )
