"""Reflection system - post-trade analysis and pattern discovery.

Inspired by TradingAgents' Reflector class. Analyzes closed positions
to identify patterns (e.g., "ADX < 15 trades lose 70%") and generates
actionable insights for future decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.db.models import (
    DEFAULT_DB_PATH,
    get_closed_positions_with_signals,
    save_reflection_insight,
    get_reflection_insights,
)
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimum sample size before generating insights
_MIN_SAMPLES = 5

# Win rate thresholds for generating insights
_POOR_WIN_RATE = 0.35      # Below this = negative insight
_GOOD_WIN_RATE = 0.65      # Above this = positive insight


@dataclass(frozen=True)
class ReflectionInsight:
    """A discovered pattern from past trades."""

    pattern: str            # e.g., "adx_low", "rsi_extreme_long"
    description: str        # Human-readable description
    sample_count: int
    win_rate: float
    avg_pnl: float
    is_positive: bool       # True = pattern works well, False = avoid


@dataclass(frozen=True)
class ReflectionReport:
    """Summary of all discovered patterns."""

    total_analyzed: int
    insights: tuple[ReflectionInsight, ...]
    overall_win_rate: float
    overall_avg_pnl: float


def _categorize_rsi(rsi: float | None) -> str:
    """Categorize RSI into buckets."""
    if rsi is None:
        return "unknown"
    if rsi < 30:
        return "oversold"
    if rsi > 70:
        return "overbought"
    if rsi < 45:
        return "low"
    if rsi > 55:
        return "high"
    return "neutral"


def _categorize_adx(adx: float | None) -> str:
    """Categorize ADX into buckets."""
    if adx is None:
        return "unknown"
    if adx < 15:
        return "weak"
    if adx < 25:
        return "moderate"
    return "strong"


def _categorize_strength(strength: float) -> str:
    """Categorize signal strength."""
    if strength >= 0.75:
        return "very_strong"
    if strength >= 0.60:
        return "strong"
    if strength >= 0.50:
        return "moderate"
    return "weak"


def _analyze_dimension(
    positions: list[dict[str, Any]],
    key_fn: Any,
    dimension_name: str,
) -> list[ReflectionInsight]:
    """Analyze one dimension (RSI, ADX, strength, etc.) for patterns."""
    buckets: dict[str, list[dict]] = {}
    for pos in positions:
        bucket = key_fn(pos)
        if bucket == "unknown":
            continue
        buckets.setdefault(bucket, []).append(pos)

    insights: list[ReflectionInsight] = []
    for bucket, bucket_positions in buckets.items():
        if len(bucket_positions) < _MIN_SAMPLES:
            continue

        wins = sum(1 for p in bucket_positions if p.get("realized_pnl", 0) > 0)
        win_rate = wins / len(bucket_positions)
        avg_pnl = sum(p.get("realized_pnl", 0) for p in bucket_positions) / len(bucket_positions)

        if win_rate <= _POOR_WIN_RATE:
            insights.append(ReflectionInsight(
                pattern=f"{dimension_name}_{bucket}",
                description=f"{dimension_name}={bucket}: {win_rate:.0%} win rate ({len(bucket_positions)} trades, avg PnL ${avg_pnl:.2f})",
                sample_count=len(bucket_positions),
                win_rate=round(win_rate, 4),
                avg_pnl=round(avg_pnl, 4),
                is_positive=False,
            ))
        elif win_rate >= _GOOD_WIN_RATE:
            insights.append(ReflectionInsight(
                pattern=f"{dimension_name}_{bucket}",
                description=f"{dimension_name}={bucket}: {win_rate:.0%} win rate ({len(bucket_positions)} trades, avg PnL ${avg_pnl:.2f})",
                sample_count=len(bucket_positions),
                win_rate=round(win_rate, 4),
                avg_pnl=round(avg_pnl, 4),
                is_positive=True,
            ))

    return insights


async def run_reflection(
    profile: str = "neutral",
    is_paper: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> ReflectionReport:
    """Analyze all closed positions and discover patterns.

    Examines multiple dimensions:
    1. RSI at entry → win rate per RSI bucket
    2. ADX at entry → win rate per trend strength
    3. Signal strength → win rate per strength bucket
    4. Direction (LONG/SHORT) → which direction works better
    5. Exit reason → which exits are most common

    Returns a ReflectionReport with discovered insights.
    """
    positions = await get_closed_positions_with_signals(
        is_paper=is_paper, profile=profile, db_path=db_path,
    )

    if not positions:
        return ReflectionReport(
            total_analyzed=0,
            insights=(),
            overall_win_rate=0.0,
            overall_avg_pnl=0.0,
        )

    total = len(positions)
    wins = sum(1 for p in positions if p.get("realized_pnl", 0) > 0)
    overall_win_rate = wins / total if total > 0 else 0
    overall_avg_pnl = sum(p.get("realized_pnl", 0) for p in positions) / total if total > 0 else 0

    all_insights: list[ReflectionInsight] = []

    # 1. RSI dimension
    all_insights.extend(_analyze_dimension(
        positions,
        lambda p: _categorize_rsi(p.get("rsi")),
        "rsi",
    ))

    # 2. ADX dimension
    all_insights.extend(_analyze_dimension(
        positions,
        lambda p: _categorize_adx(p.get("adx")),
        "adx",
    ))

    # 3. Signal strength dimension
    all_insights.extend(_analyze_dimension(
        positions,
        lambda p: _categorize_strength(p.get("strength", 0)),
        "strength",
    ))

    # 4. Direction dimension
    all_insights.extend(_analyze_dimension(
        positions,
        lambda p: p.get("direction", "NEUTRAL"),
        "direction",
    ))

    # 5. Exit reason dimension
    all_insights.extend(_analyze_dimension(
        positions,
        lambda p: p.get("exit_reason", "unknown"),
        "exit",
    ))

    # Save insights to DB
    for insight in all_insights:
        await save_reflection_insight(
            pattern=insight.pattern,
            description=insight.description,
            sample_count=insight.sample_count,
            win_rate=insight.win_rate,
            avg_pnl=insight.avg_pnl,
            is_positive=insight.is_positive,
            profile=profile,
            db_path=db_path,
        )

    logger.info(
        "Reflection [%s]: analyzed %d positions, found %d insights (win_rate=%.1f%%)",
        profile, total, len(all_insights), overall_win_rate * 100,
    )

    return ReflectionReport(
        total_analyzed=total,
        insights=tuple(all_insights),
        overall_win_rate=round(overall_win_rate, 4),
        overall_avg_pnl=round(overall_avg_pnl, 4),
    )


async def get_insights_for_signal(
    direction: str,
    rsi: float | None = None,
    adx: float | None = None,
    strength: float = 0,
    profile: str = "neutral",
    db_path: Path = DEFAULT_DB_PATH,
) -> list[ReflectionInsight]:
    """Get relevant negative insights for a proposed trade.

    Used before entry to check if any discovered patterns
    suggest this trade type has historically performed poorly.
    """
    stored = await get_reflection_insights(
        profile=profile, is_positive=False, db_path=db_path,
    )

    relevant: list[ReflectionInsight] = []
    rsi_cat = _categorize_rsi(rsi)
    adx_cat = _categorize_adx(adx)
    str_cat = _categorize_strength(strength)

    for row in stored:
        pattern = row["pattern"]
        # Check if this insight matches current trade conditions
        if pattern == f"rsi_{rsi_cat}":
            relevant.append(_row_to_insight(row))
        elif pattern == f"adx_{adx_cat}":
            relevant.append(_row_to_insight(row))
        elif pattern == f"strength_{str_cat}":
            relevant.append(_row_to_insight(row))
        elif pattern == f"direction_{direction}":
            relevant.append(_row_to_insight(row))

    if relevant:
        logger.warning(
            "Reflection warnings for %s (rsi=%s, adx=%s, str=%s): %s",
            direction, rsi_cat, adx_cat, str_cat,
            [i.pattern for i in relevant],
        )

    return relevant


def _row_to_insight(row: dict[str, Any]) -> ReflectionInsight:
    """Convert a DB row to ReflectionInsight."""
    return ReflectionInsight(
        pattern=row["pattern"],
        description=row["description"],
        sample_count=row["sample_count"],
        win_rate=row["win_rate"],
        avg_pnl=row["avg_pnl"],
        is_positive=bool(row["is_positive"]),
    )
