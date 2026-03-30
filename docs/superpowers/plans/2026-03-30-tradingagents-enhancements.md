# TradingAgents-Inspired Enhancements Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate 5 key architectural patterns from TradingAgents into the existing deterministic trading bot without requiring LLMs.

**Architecture:** Each feature adds a new module under `src/` that plugs into the existing pipeline (Scan → Analyze → Risk → Trade). All features are rule-based (no LLM), maintain determinism, and follow the frozen dataclass / immutable config patterns.

**Tech Stack:** Python 3.11+, aiosqlite, rank-bm25, existing indicator/signal infrastructure

---

## Feature 1: Adversarial Validation (Bull/Bear)

### Task 1.1: Create adversarial validator module

**Files:**
- Create: `src/strategy/adversarial.py`
- Test: `tests/test_adversarial.py`

The adversarial validator checks if the opposite case has strong evidence. For a LONG signal, it checks bearish indicators; for SHORT, bullish ones.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_adversarial.py
"""Tests for adversarial signal validation (Bull/Bear debate)."""
import pytest
from src.strategy.adversarial import validate_signal, AdversarialResult

class TestAdversarialValidation:
    def test_long_signal_no_opposition(self):
        """LONG signal with no bearish evidence should pass."""
        result = validate_signal(
            direction="LONG",
            details={
                "rsi": {"direction": "LONG", "reason": "oversold (25.0)"},
                "macd": {"direction": "LONG", "reason": "bullish crossover"},
                "ema_trend": {"direction": "LONG", "reason": "above 200 EMA"},
                "bollinger": {"direction": "NEUTRAL", "reason": "mid-band"},
                "stochastic": {"direction": "LONG", "reason": "oversold"},
                "volume": {"direction": "LONG", "reason": "high volume bullish"},
                "adx": {"direction": "LONG", "reason": "strong uptrend"},
                "ema_cross": {"direction": "LONG", "reason": "golden cross"},
            },
            rsi=25.0,
            adx=30.0,
        )
        assert result.passed is True
        assert result.penalty == 0.0
        assert result.bear_count == 0

    def test_long_signal_strong_opposition(self):
        """LONG signal with 3+ bearish indicators should be penalized."""
        result = validate_signal(
            direction="LONG",
            details={
                "rsi": {"direction": "SHORT", "reason": "overbought (75.0)"},
                "macd": {"direction": "SHORT", "reason": "bearish crossover"},
                "ema_trend": {"direction": "SHORT", "reason": "below 200 EMA"},
                "bollinger": {"direction": "SHORT", "reason": "at upper band"},
                "stochastic": {"direction": "LONG", "reason": "neutral"},
                "volume": {"direction": "LONG", "reason": "high volume"},
                "adx": {"direction": "LONG", "reason": "strong trend"},
                "ema_cross": {"direction": "LONG", "reason": "golden cross"},
            },
            rsi=75.0,
            adx=30.0,
        )
        assert result.passed is False  # 4 opposing = reject
        assert result.bear_count == 4

    def test_short_signal_with_bull_opposition(self):
        """SHORT signal checked against bullish evidence."""
        result = validate_signal(
            direction="SHORT",
            details={
                "rsi": {"direction": "LONG", "reason": "oversold (25.0)"},
                "macd": {"direction": "LONG", "reason": "bullish crossover"},
                "ema_trend": {"direction": "SHORT", "reason": "below 200 EMA"},
                "bollinger": {"direction": "SHORT", "reason": "at lower band"},
                "stochastic": {"direction": "SHORT", "reason": "overbought"},
                "volume": {"direction": "SHORT", "reason": "high volume bearish"},
                "adx": {"direction": "SHORT", "reason": "strong downtrend"},
                "ema_cross": {"direction": "SHORT", "reason": "death cross"},
            },
            rsi=25.0,
            adx=30.0,
        )
        assert result.passed is True
        assert result.bull_count == 2
        assert result.penalty > 0  # 2 opposing = minor penalty

    def test_neutral_signal_passes(self):
        """NEUTRAL signal should always pass (no trade = no risk)."""
        result = validate_signal(
            direction="NEUTRAL",
            details={},
            rsi=50.0,
            adx=10.0,
        )
        assert result.passed is True
        assert result.penalty == 0.0

    def test_rsi_extreme_adds_penalty(self):
        """RSI at extreme opposite adds extra penalty for LONG."""
        result = validate_signal(
            direction="LONG",
            details={
                "rsi": {"direction": "SHORT", "reason": "overbought (80.0)"},
                "macd": {"direction": "LONG", "reason": "bullish"},
                "ema_trend": {"direction": "LONG", "reason": "above 200"},
                "bollinger": {"direction": "NEUTRAL", "reason": "mid-band"},
                "stochastic": {"direction": "SHORT", "reason": "overbought"},
                "volume": {"direction": "LONG", "reason": "high volume"},
                "adx": {"direction": "LONG", "reason": "strong"},
                "ema_cross": {"direction": "LONG", "reason": "golden"},
            },
            rsi=80.0,
            adx=30.0,
        )
        assert result.penalty > 0.10  # RSI extreme adds extra

    def test_weak_trend_reduces_penalty(self):
        """Low ADX (weak trend) should reduce opposition impact."""
        result = validate_signal(
            direction="LONG",
            details={
                "rsi": {"direction": "SHORT", "reason": "above midline (65)"},
                "macd": {"direction": "SHORT", "reason": "bearish"},
                "ema_trend": {"direction": "LONG", "reason": "above 200"},
                "bollinger": {"direction": "NEUTRAL", "reason": "mid"},
                "stochastic": {"direction": "NEUTRAL", "reason": "neutral"},
                "volume": {"direction": "LONG", "reason": "high"},
                "adx": {"direction": "NEUTRAL", "reason": "weak (12)"},
                "ema_cross": {"direction": "LONG", "reason": "golden"},
            },
            rsi=65.0,
            adx=12.0,
        )
        # Weak trend → opposition is less meaningful
        assert result.penalty < 0.20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_adversarial.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement adversarial validator**

```python
# src/strategy/adversarial.py
"""Adversarial signal validation - checks opposing evidence before entry."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Weighted importance of opposing indicators
_OPPOSITION_WEIGHTS: dict[str, float] = {
    "macd": 2.0,       # MACD opposition is most significant
    "rsi": 1.5,
    "ema_trend": 1.5,
    "bollinger": 1.0,
    "ema_cross": 1.0,
    "stochastic": 1.0,
    "volume": 0.5,
    "adx": 0.5,
}

# Thresholds
_REJECT_OPPOSING_COUNT = 4      # 4+ opposing indicators = reject
_PENALTY_PER_OPPOSING = 0.05    # 5% penalty per opposing indicator
_RSI_EXTREME_LONG = 75.0        # RSI above this while LONG = extra penalty
_RSI_EXTREME_SHORT = 25.0       # RSI below this while SHORT = extra penalty
_RSI_EXTREME_PENALTY = 0.10     # Extra penalty for RSI at extremes
_WEAK_TREND_ADX = 15.0          # ADX below this = weak trend, reduce penalty


@dataclass(frozen=True)
class AdversarialResult:
    """Result of adversarial validation."""

    passed: bool
    penalty: float          # 0.0 ~ 1.0 strength reduction
    bear_count: int = 0     # opposing indicators for LONG
    bull_count: int = 0     # opposing indicators for SHORT
    reasons: tuple[str, ...] = ()


def validate_signal(
    direction: str,
    details: dict[str, Any],
    rsi: float = 50.0,
    adx: float = 25.0,
) -> AdversarialResult:
    """Validate a signal by checking opposing evidence.

    For LONG signals, counts bearish (SHORT) indicators.
    For SHORT signals, counts bullish (LONG) indicators.

    Returns AdversarialResult with pass/fail and penalty amount.
    """
    if direction == "NEUTRAL":
        return AdversarialResult(passed=True, penalty=0.0)

    opposite = "SHORT" if direction == "LONG" else "LONG"
    opposing_count = 0
    weighted_opposition = 0.0
    reasons: list[str] = []

    for indicator, vote in details.items():
        if vote.get("direction") == opposite:
            opposing_count += 1
            weight = _OPPOSITION_WEIGHTS.get(indicator, 0.5)
            weighted_opposition += weight
            reasons.append(f"{indicator}: {vote.get('reason', 'opposing')}")

    # Base penalty from opposing count
    penalty = opposing_count * _PENALTY_PER_OPPOSING

    # RSI extreme penalty
    if direction == "LONG" and rsi > _RSI_EXTREME_LONG:
        penalty += _RSI_EXTREME_PENALTY
        reasons.append(f"RSI extreme for LONG ({rsi:.1f})")
    elif direction == "SHORT" and rsi < _RSI_EXTREME_SHORT:
        penalty += _RSI_EXTREME_PENALTY
        reasons.append(f"RSI extreme for SHORT ({rsi:.1f})")

    # Weak trend reduces penalty (opposition less meaningful in choppy market)
    if adx < _WEAK_TREND_ADX:
        trend_factor = adx / _WEAK_TREND_ADX  # 0.0 ~ 1.0
        penalty *= trend_factor

    penalty = min(1.0, round(penalty, 4))

    # Reject if too many opposing indicators
    passed = opposing_count < _REJECT_OPPOSING_COUNT

    bear_count = opposing_count if direction == "LONG" else 0
    bull_count = opposing_count if direction == "SHORT" else 0

    if not passed:
        logger.info(
            "Adversarial REJECTED %s: %d opposing indicators [%s]",
            direction, opposing_count, ", ".join(reasons),
        )
    elif penalty > 0:
        logger.info(
            "Adversarial penalty for %s: -%.1f%% (%d opposing) [%s]",
            direction, penalty * 100, opposing_count, ", ".join(reasons),
        )

    return AdversarialResult(
        passed=passed,
        penalty=penalty,
        bear_count=bear_count,
        bull_count=bull_count,
        reasons=tuple(reasons),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_adversarial.py -v`
Expected: All PASS

- [ ] **Step 5: Integrate into analyzer**

Modify: `src/strategy/analyzer.py` — add adversarial validation after quality filters, before returning result.

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All existing + new tests PASS

- [ ] **Step 7: Commit**

```
feat: add adversarial signal validation (bull/bear debate)
```

---

## Feature 2: BM25 Situation Memory

### Task 2.1: Create situation memory module

**Files:**
- Create: `src/memory/situation_memory.py`
- Create: `src/memory/__init__.py`
- Test: `tests/test_situation_memory.py`
- Modify: `src/db/models.py` (add situation_outcomes table)

BM25-based memory that stores past trading situations and their outcomes, then retrieves similar situations to inform new decisions.

- [ ] **Step 1: Write failing tests**

Tests for storing situations, retrieving similar ones, and calculating win rates.

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add situation_outcomes table to DB**

Add new table to `src/db/models.py`:
```sql
CREATE TABLE IF NOT EXISTS situation_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    situation_text TEXT NOT NULL,
    indicator_snapshot TEXT NOT NULL,
    strength REAL NOT NULL,
    confirming_count INTEGER,
    realized_pnl REAL,
    exit_reason TEXT,
    is_win INTEGER,
    profile TEXT NOT NULL DEFAULT 'neutral',
    created_at TEXT DEFAULT (datetime('now'))
)
```

- [ ] **Step 4: Implement BM25 situation memory**

- [ ] **Step 5: Run tests to verify they pass**

- [ ] **Step 6: Integrate into orchestrator pipeline**

After risk check passes, query memory for similar situations. If similar situation win rate < 40%, reduce position size by 30%.

- [ ] **Step 7: Add situation recording on position close**

Modify position monitor/paper trader to record situation outcome when position closes.

- [ ] **Step 8: Run full test suite**

- [ ] **Step 9: Commit**

```
feat: add BM25 situation memory for learning from past trades
```

---

## Feature 3: News/Sentiment Filter

### Task 3.1: Create sentiment filter module

**Files:**
- Create: `src/scanner/sentiment_filter.py`
- Test: `tests/test_sentiment_filter.py`
- Modify: `config/settings.py` (add sentiment settings)

Uses CoinGecko API (free, no key needed) to fetch trending coins and market sentiment.

- [ ] **Step 1: Write failing tests**

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add sentiment settings to config**

- [ ] **Step 4: Implement sentiment filter**

- [ ] **Step 5: Run tests to verify they pass**

- [ ] **Step 6: Integrate into scanner as 9th filter**

- [ ] **Step 7: Run full test suite**

- [ ] **Step 8: Commit**

```
feat: add news/sentiment filter using CoinGecko fear & greed index
```

---

## Feature 4: Multi-Perspective Risk Scoring

### Task 4.1: Create multi-perspective risk evaluator

**Files:**
- Create: `src/risk/perspectives.py`
- Test: `tests/test_risk_perspectives.py`
- Modify: `src/strategy/orchestrator.py` (use perspective score for position sizing)

Three risk perspectives (aggressive/neutral/conservative) that score each trade and adjust position size.

- [ ] **Step 1: Write failing tests**

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement perspective evaluator**

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Integrate into orchestrator for position size adjustment**

- [ ] **Step 6: Run full test suite**

- [ ] **Step 7: Commit**

```
feat: add multi-perspective risk scoring for position sizing
```

---

## Feature 5: Reflection System

### Task 5.1: Create reflection analyzer

**Files:**
- Create: `src/strategy/reflection.py`
- Test: `tests/test_reflection.py`
- Modify: `src/db/models.py` (add reflection_insights table)
- Modify: `src/trading/position_monitor.py` (trigger reflection on close)

Analyzes closed positions to identify patterns (e.g., "ADX < 20 trades lose 70%") and generates insights that adjust future thresholds.

- [ ] **Step 1: Write failing tests**

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add reflection_insights table**

- [ ] **Step 4: Implement reflection analyzer**

- [ ] **Step 5: Run tests to verify they pass**

- [ ] **Step 6: Integrate into position close flow**

- [ ] **Step 7: Run full test suite**

- [ ] **Step 8: Commit**

```
feat: add reflection system for post-trade analysis and learning
```
