"""Overfitting statistics."""

from __future__ import annotations

import numpy as np
import pytest

from src.trend.backtest.stats import daily_returns, deflated_sharpe, max_drawdown, pbo_cscv, sharpe


def test_daily_returns_compound_bars():
    ts = np.array([0, 14400, 86400])
    days, r = daily_returns(ts, np.array([0.1, 0.1, -0.5]))
    assert days.tolist() == [0, 86400]
    assert r == pytest.approx([0.21, -0.5])


def test_sharpe_and_drawdown():
    r = np.array([0.01, -0.01, 0.02, -0.02])
    assert sharpe(r) == pytest.approx(0.0)
    assert max_drawdown(np.array([0.1, -0.5, 0.2])) == pytest.approx(-0.5)


def test_dsr_penalizes_more_trials():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, 1000)
    assert deflated_sharpe(r, 1000, 1e-3) < deflated_sharpe(r, 2, 1e-3)


def test_dsr_high_for_strong_single_strategy():
    rng = np.random.default_rng(0)
    assert deflated_sharpe(rng.normal(0.003, 0.01, 1500), 1, 0.0) > 0.99


def test_pbo_high_for_noise_low_for_real_skill():
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 0.01, (1600, 20))
    assert pbo_cscv(noise, n_blocks=8) > 0.4         # no skill -> IS winner does not persist
    skilled = noise.copy()
    skilled[:, 0] += 0.004
    assert pbo_cscv(skilled, n_blocks=8) < 0.05
