"""Tests for BM25-based situation memory."""

import pytest
from pathlib import Path

from src.db.models import init_db, save_situation_outcome, get_situation_outcomes
from src.memory.situation_memory import (
    build_situation_text,
    record_situation,
    query_similar_situations,
    MemoryQuery,
)

TEST_DB = Path(__file__).parent / "test_memory.db"


def _make_details(direction: str = "LONG") -> dict:
    """Build sample indicator details."""
    opp = "SHORT" if direction == "LONG" else "LONG"
    return {
        "rsi": {"direction": direction, "reason": "oversold (25.0)"},
        "macd": {"direction": direction, "reason": "bullish crossover"},
        "ema_trend": {"direction": direction, "reason": "above 200 EMA"},
        "bollinger": {"direction": "NEUTRAL", "reason": "mid-band"},
        "ema_cross": {"direction": direction, "reason": "golden cross"},
        "stochastic": {"direction": direction, "reason": "oversold"},
        "volume": {"direction": direction, "reason": "high volume bullish"},
        "adx": {"direction": direction, "reason": "strong uptrend"},
    }


class TestBuildSituationText:
    def test_basic_text(self):
        text = build_situation_text(
            symbol="BTCUSDT",
            direction="LONG",
            strength=0.75,
            confirming_count=6,
            details=_make_details(),
            rsi=25.0,
            adx=30.0,
        )
        assert "symbol:BTCUSDT" in text
        assert "direction:LONG" in text
        assert "strength:0.75" in text
        assert "rsi:oversold" in text
        assert "trend:strong" in text

    def test_rsi_categories(self):
        for rsi, expected in [(25, "oversold"), (35, "low"), (50, "neutral"),
                              (65, "high"), (75, "overbought")]:
            text = build_situation_text(
                "TEST", "LONG", 0.5, 4, {}, rsi=float(rsi),
            )
            assert f"rsi:{expected}" in text

    def test_trend_categories(self):
        for adx, expected in [(30, "strong"), (20, "moderate"), (10, "weak")]:
            text = build_situation_text(
                "TEST", "LONG", 0.5, 4, {}, adx=float(adx),
            )
            assert f"trend:{expected}" in text

    def test_keywords_extracted(self):
        details = {"macd": {"direction": "LONG", "reason": "bullish crossover"}}
        text = build_situation_text("TEST", "LONG", 0.5, 4, details)
        assert "crossover" in text

    def test_no_rsi_or_adx(self):
        text = build_situation_text("TEST", "LONG", 0.5, 4, {})
        assert "rsi:" not in text
        assert "trend:" not in text


@pytest.fixture(autouse=True)
async def setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    await init_db(db_path=TEST_DB)
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()


class TestSituationMemoryDB:
    async def test_save_and_get(self):
        await save_situation_outcome(
            symbol="BTCUSDT",
            direction="LONG",
            situation_text="symbol:BTCUSDT direction:LONG rsi:oversold",
            strength=0.75,
            realized_pnl=5.0,
            is_win=1,
            db_path=TEST_DB,
        )
        results = await get_situation_outcomes(profile="neutral", db_path=TEST_DB)
        assert len(results) == 1
        assert results[0]["symbol"] == "BTCUSDT"
        assert results[0]["is_win"] == 1

    async def test_empty_results(self):
        results = await get_situation_outcomes(profile="neutral", db_path=TEST_DB)
        assert results == []


class TestRecordSituation:
    async def test_record(self):
        await record_situation(
            symbol="ETHUSDT",
            direction="SHORT",
            strength=0.65,
            confirming_count=5,
            details=_make_details("SHORT"),
            realized_pnl=-2.5,
            exit_reason="stop_loss",
            profile="neutral",
            rsi=72.0,
            adx=28.0,
            db_path=TEST_DB,
        )
        results = await get_situation_outcomes(profile="neutral", db_path=TEST_DB)
        assert len(results) == 1
        assert results[0]["direction"] == "SHORT"
        assert results[0]["is_win"] == 0


class TestQuerySimilarSituations:
    async def _seed_situations(self, count: int = 10, win_pct: float = 0.5):
        """Seed DB with N situations."""
        for i in range(count):
            is_win = i < (count * win_pct)
            pnl = 5.0 if is_win else -3.0
            await save_situation_outcome(
                symbol="BTCUSDT",
                direction="LONG",
                situation_text=(
                    f"symbol:BTCUSDT direction:LONG strength:0.7{i % 10} "
                    f"rsi:oversold trend:strong macd:LONG crossover "
                    f"ema_trend:LONG confirming:{5 + i % 3}"
                ),
                strength=0.70 + (i % 10) * 0.01,
                realized_pnl=pnl,
                is_win=1 if is_win else 0,
                db_path=TEST_DB,
            )

    async def test_insufficient_data_returns_default(self):
        """With < 5 situations, memory should not influence decisions."""
        await self._seed_situations(count=3)
        result = await query_similar_situations(
            symbol="BTCUSDT", direction="LONG", strength=0.75,
            confirming_count=6, details=_make_details(),
            profile="neutral", rsi=25.0, adx=30.0,
            db_path=TEST_DB,
        )
        assert result.similar_count == 0
        assert result.scale_factor == 1.0
        assert result.should_reduce is False

    async def test_high_win_rate_no_reduction(self):
        """High win rate situations → no position reduction."""
        await self._seed_situations(count=10, win_pct=0.8)
        result = await query_similar_situations(
            symbol="BTCUSDT", direction="LONG", strength=0.75,
            confirming_count=6, details=_make_details(),
            profile="neutral", rsi=25.0, adx=30.0,
            db_path=TEST_DB,
        )
        assert result.similar_count > 0
        assert result.should_reduce is False
        assert result.scale_factor == 1.0

    async def test_low_win_rate_triggers_reduction(self):
        """Low win rate → should reduce position."""
        await self._seed_situations(count=10, win_pct=0.1)
        result = await query_similar_situations(
            symbol="BTCUSDT", direction="LONG", strength=0.75,
            confirming_count=6, details=_make_details(),
            profile="neutral", rsi=25.0, adx=30.0,
            db_path=TEST_DB,
        )
        assert result.similar_count > 0
        assert result.should_reduce is True
        assert result.scale_factor == 0.70

    async def test_memory_query_is_frozen(self):
        """MemoryQuery should be immutable."""
        result = MemoryQuery(
            similar_count=0, similar_win_rate=0.0,
            should_reduce=False, scale_factor=1.0,
        )
        with pytest.raises(AttributeError):
            result.should_reduce = True  # type: ignore[misc]

    async def test_profile_isolation(self):
        """Situations from different profiles should not mix."""
        await self._seed_situations(count=10, win_pct=0.1)
        # Query with different profile
        result = await query_similar_situations(
            symbol="BTCUSDT", direction="LONG", strength=0.75,
            confirming_count=6, details=_make_details(),
            profile="aggressive", rsi=25.0, adx=30.0,
            db_path=TEST_DB,
        )
        assert result.similar_count == 0  # No data for aggressive
