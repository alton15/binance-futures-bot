"""Tests for sentiment filter."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.scanner.sentiment_filter import (
    SentimentData,
    SentimentAdjustment,
    evaluate_sentiment,
    fetch_sentiment,
    reset_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    reset_cache()
    yield
    reset_cache()


class TestEvaluateSentiment:
    def test_extreme_fear_boosts_long(self):
        sentiment = SentimentData(index=15, label="Extreme Fear", timestamp=0, available=True)
        result = evaluate_sentiment("LONG", sentiment)
        assert result.adjustment == 0.10
        assert "extreme fear" in result.reason
        assert "boosts LONG" in result.reason

    def test_fear_boosts_long(self):
        sentiment = SentimentData(index=30, label="Fear", timestamp=0, available=True)
        result = evaluate_sentiment("LONG", sentiment)
        assert result.adjustment == 0.05
        assert "fear" in result.reason

    def test_extreme_greed_penalizes_long(self):
        sentiment = SentimentData(index=85, label="Extreme Greed", timestamp=0, available=True)
        result = evaluate_sentiment("LONG", sentiment)
        assert result.adjustment == -0.10
        assert "extreme greed" in result.reason
        assert "penalizes LONG" in result.reason

    def test_greed_penalizes_long(self):
        sentiment = SentimentData(index=70, label="Greed", timestamp=0, available=True)
        result = evaluate_sentiment("LONG", sentiment)
        assert result.adjustment == -0.05

    def test_extreme_greed_boosts_short(self):
        sentiment = SentimentData(index=85, label="Extreme Greed", timestamp=0, available=True)
        result = evaluate_sentiment("SHORT", sentiment)
        assert result.adjustment == 0.10
        assert "boosts SHORT" in result.reason

    def test_greed_boosts_short(self):
        sentiment = SentimentData(index=70, label="Greed", timestamp=0, available=True)
        result = evaluate_sentiment("SHORT", sentiment)
        assert result.adjustment == 0.05

    def test_extreme_fear_penalizes_short(self):
        sentiment = SentimentData(index=10, label="Extreme Fear", timestamp=0, available=True)
        result = evaluate_sentiment("SHORT", sentiment)
        assert result.adjustment == -0.10
        assert "penalizes SHORT" in result.reason

    def test_fear_penalizes_short(self):
        sentiment = SentimentData(index=30, label="Fear", timestamp=0, available=True)
        result = evaluate_sentiment("SHORT", sentiment)
        assert result.adjustment == -0.05

    def test_neutral_sentiment_no_adjustment(self):
        sentiment = SentimentData(index=50, label="Neutral", timestamp=0, available=True)
        result = evaluate_sentiment("LONG", sentiment)
        assert result.adjustment == 0.0
        assert "neutral sentiment" in result.reason

    def test_unavailable_sentiment_no_adjustment(self):
        sentiment = SentimentData(index=50, label="Unavailable", timestamp=0, available=False)
        result = evaluate_sentiment("LONG", sentiment)
        assert result.adjustment == 0.0
        assert "unavailable" in result.reason

    def test_neutral_direction_no_adjustment(self):
        sentiment = SentimentData(index=10, label="Extreme Fear", timestamp=0, available=True)
        result = evaluate_sentiment("NEUTRAL", sentiment)
        assert result.adjustment == 0.0

    def test_result_is_frozen(self):
        sentiment = SentimentData(index=50, label="Neutral", timestamp=0, available=True)
        result = evaluate_sentiment("LONG", sentiment)
        with pytest.raises(AttributeError):
            result.adjustment = 0.5  # type: ignore[misc]

    def test_boundary_fear_20(self):
        """Index=20 is still extreme fear."""
        sentiment = SentimentData(index=20, label="Extreme Fear", timestamp=0, available=True)
        result = evaluate_sentiment("LONG", sentiment)
        assert result.adjustment == 0.10

    def test_boundary_greed_65(self):
        """Index=65 is greed."""
        sentiment = SentimentData(index=65, label="Greed", timestamp=0, available=True)
        result = evaluate_sentiment("LONG", sentiment)
        assert result.adjustment == -0.05

    def test_boundary_neutral_36(self):
        """Index=36 is neutral (between fear=35 and greed=65)."""
        sentiment = SentimentData(index=36, label="Neutral", timestamp=0, available=True)
        result = evaluate_sentiment("LONG", sentiment)
        assert result.adjustment == 0.0


class TestFetchSentiment:
    async def test_successful_fetch(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [{"value": "25", "value_classification": "Fear", "timestamp": "1000"}]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("src.scanner.sentiment_filter.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_sentiment()

        assert result.available is True
        assert result.index == 25
        assert result.label == "Fear"

    async def test_failed_fetch_returns_unavailable(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        with patch("src.scanner.sentiment_filter.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_sentiment()

        assert result.available is False
        assert result.index == 50  # Default neutral

    async def test_cache_hit(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [{"value": "75", "value_classification": "Greed", "timestamp": "1000"}]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("src.scanner.sentiment_filter.httpx.AsyncClient", return_value=mock_client):
            result1 = await fetch_sentiment()
            result2 = await fetch_sentiment()

        # Should only call API once (second is cached)
        assert mock_client.get.call_count == 1
        assert result1.index == result2.index
