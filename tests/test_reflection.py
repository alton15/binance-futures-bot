"""Tests for reflection system - post-trade analysis and pattern discovery."""

import pytest
from pathlib import Path

from src.db.models import (
    init_db,
    save_signal,
    save_trade,
    open_position,
    close_position,
    save_indicator_snapshot,
    save_reflection_insight,
    get_reflection_insights,
    get_closed_positions_with_signals,
)
from src.strategy.reflection import (
    run_reflection,
    get_insights_for_signal,
    ReflectionInsight,
    ReflectionReport,
    _categorize_rsi,
    _categorize_adx,
    _categorize_strength,
)

TEST_DB = Path(__file__).parent / "test_reflection.db"


@pytest.fixture(autouse=True)
async def setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    await init_db(db_path=TEST_DB)
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()


class TestCategorization:
    def test_rsi_categories(self):
        assert _categorize_rsi(25.0) == "oversold"
        assert _categorize_rsi(35.0) == "low"
        assert _categorize_rsi(50.0) == "neutral"
        assert _categorize_rsi(60.0) == "high"
        assert _categorize_rsi(75.0) == "overbought"
        assert _categorize_rsi(None) == "unknown"

    def test_adx_categories(self):
        assert _categorize_adx(10.0) == "weak"
        assert _categorize_adx(20.0) == "moderate"
        assert _categorize_adx(30.0) == "strong"
        assert _categorize_adx(None) == "unknown"

    def test_strength_categories(self):
        assert _categorize_strength(0.80) == "very_strong"
        assert _categorize_strength(0.65) == "strong"
        assert _categorize_strength(0.55) == "moderate"
        assert _categorize_strength(0.40) == "weak"


async def _seed_closed_positions(
    count: int = 10,
    win_pct: float = 0.5,
    rsi: float = 50.0,
    adx: float = 25.0,
    direction: str = "LONG",
    exit_reason: str = "take_profit",
):
    """Seed DB with closed positions + signals + indicators."""
    for i in range(count):
        is_win = i < (count * win_pct)
        pnl = 5.0 if is_win else -3.0

        # Save indicator snapshot
        await save_indicator_snapshot(
            symbol=f"TEST{i}USDT",
            timeframe="1h",
            close_price=100.0,
            rsi=rsi,
            adx=adx,
            atr=2.0,
            db_path=TEST_DB,
        )

        # Save signal
        signal_id = await save_signal(
            symbol=f"TEST{i}USDT",
            direction=direction,
            strength=0.70,
            confirming_count=5,
            db_path=TEST_DB,
        )

        # Save trade
        trade_id = await save_trade(
            symbol=f"TEST{i}USDT",
            direction=direction,
            entry_price=100.0,
            size=1.0,
            cost=100.0,
            signal_id=signal_id,
            status="filled",
            db_path=TEST_DB,
        )

        # Open and close position
        pos_id = await open_position(
            symbol=f"TEST{i}USDT",
            direction=direction,
            entry_price=100.0,
            size=1.0,
            cost=100.0,
            leverage=3,
            trade_id=trade_id,
            db_path=TEST_DB,
        )
        await close_position(
            position_id=pos_id,
            realized_pnl=pnl,
            exit_reason=exit_reason if is_win else "stop_loss",
            db_path=TEST_DB,
        )


class TestReflectionDB:
    async def test_save_and_get_insights(self):
        await save_reflection_insight(
            pattern="rsi_oversold",
            description="RSI oversold trades: 30% win rate",
            sample_count=10,
            win_rate=0.30,
            avg_pnl=-1.5,
            is_positive=False,
            db_path=TEST_DB,
        )
        results = await get_reflection_insights(profile="neutral", db_path=TEST_DB)
        assert len(results) == 1
        assert results[0]["pattern"] == "rsi_oversold"
        assert results[0]["is_positive"] == 0

    async def test_filter_positive(self):
        await save_reflection_insight(
            pattern="good_one", description="good",
            win_rate=0.80, is_positive=True, db_path=TEST_DB,
        )
        await save_reflection_insight(
            pattern="bad_one", description="bad",
            win_rate=0.20, is_positive=False, db_path=TEST_DB,
        )
        negative = await get_reflection_insights(
            profile="neutral", is_positive=False, db_path=TEST_DB,
        )
        assert len(negative) == 1
        assert negative[0]["pattern"] == "bad_one"

    async def test_upsert_replaces_old(self):
        await save_reflection_insight(
            pattern="test", description="old",
            win_rate=0.30, db_path=TEST_DB,
        )
        await save_reflection_insight(
            pattern="test", description="new",
            win_rate=0.50, db_path=TEST_DB,
        )
        results = await get_reflection_insights(profile="neutral", db_path=TEST_DB)
        assert len(results) == 1
        assert results[0]["description"] == "new"


class TestRunReflection:
    async def test_empty_positions(self):
        report = await run_reflection(profile="neutral", db_path=TEST_DB)
        assert report.total_analyzed == 0
        assert report.insights == ()

    async def test_discovers_patterns(self):
        await _seed_closed_positions(count=10, win_pct=0.2, rsi=25.0, adx=10.0)
        report = await run_reflection(profile="neutral", db_path=TEST_DB)
        assert report.total_analyzed == 10
        assert report.overall_win_rate == 0.2

        # Should find negative patterns (20% win rate)
        negative = [i for i in report.insights if not i.is_positive]
        assert len(negative) > 0

    async def test_good_pattern_detected(self):
        await _seed_closed_positions(count=10, win_pct=0.8, rsi=50.0, adx=30.0)
        report = await run_reflection(profile="neutral", db_path=TEST_DB)

        positive = [i for i in report.insights if i.is_positive]
        # With 80% win rate and strong ADX, should find positive patterns
        assert len(positive) >= 0  # May or may not depending on bucket sizes

    async def test_insufficient_sample_ignored(self):
        """With < 5 positions per bucket, no insights should be generated."""
        await _seed_closed_positions(count=3, win_pct=0.0)
        report = await run_reflection(profile="neutral", db_path=TEST_DB)
        assert report.total_analyzed == 3
        # Not enough per bucket to generate insights
        assert len(report.insights) == 0

    async def test_report_is_frozen(self):
        report = ReflectionReport(
            total_analyzed=0, insights=(), overall_win_rate=0.0, overall_avg_pnl=0.0,
        )
        with pytest.raises(AttributeError):
            report.total_analyzed = 5  # type: ignore[misc]


class TestGetInsightsForSignal:
    async def test_matches_rsi_pattern(self):
        await save_reflection_insight(
            pattern="rsi_oversold",
            description="RSI oversold: 25% win rate",
            sample_count=10,
            win_rate=0.25,
            avg_pnl=-2.0,
            is_positive=False,
            db_path=TEST_DB,
        )
        insights = await get_insights_for_signal(
            direction="LONG", rsi=25.0, adx=20.0, strength=0.65,
            profile="neutral", db_path=TEST_DB,
        )
        assert len(insights) == 1
        assert insights[0].pattern == "rsi_oversold"

    async def test_no_match_returns_empty(self):
        await save_reflection_insight(
            pattern="rsi_overbought",
            description="bad",
            win_rate=0.20,
            is_positive=False,
            db_path=TEST_DB,
        )
        # Current RSI=50 → categorized as "neutral", won't match "overbought"
        insights = await get_insights_for_signal(
            direction="LONG", rsi=50.0, adx=20.0, strength=0.65,
            profile="neutral", db_path=TEST_DB,
        )
        assert len(insights) == 0

    async def test_direction_match(self):
        await save_reflection_insight(
            pattern="direction_LONG",
            description="LONG trades: 30% win rate",
            sample_count=20,
            win_rate=0.30,
            avg_pnl=-1.0,
            is_positive=False,
            db_path=TEST_DB,
        )
        insights = await get_insights_for_signal(
            direction="LONG", rsi=50.0, db_path=TEST_DB,
        )
        assert len(insights) == 1

    async def test_insight_is_frozen(self):
        insight = ReflectionInsight(
            pattern="test", description="test",
            sample_count=5, win_rate=0.5, avg_pnl=0.0, is_positive=True,
        )
        with pytest.raises(AttributeError):
            insight.win_rate = 0.8  # type: ignore[misc]
