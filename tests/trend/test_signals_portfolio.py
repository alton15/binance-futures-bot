"""Trend signals, sizing and the portfolio simulator."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from src.trend.config import BASELINE_FUNDING_PER_BAR, CostConfig, PortfolioConfig, SignalConfig
from src.trend.portfolio import apply_band, simulate, target_weights, universe_mask
from src.trend.signals import compute_signal, donchian_vote, ema_vote, tsmom_vote

T = 400


def _trend(slope: float) -> np.ndarray:
    return (100 * np.exp(slope * np.arange(T)))[:, None].astype(np.float32)


class TestSignals:
    def test_donchian_long_in_uptrend_short_in_downtrend(self):
        up, down = _trend(0.002), _trend(-0.002)
        assert donchian_vote(up, up, up, 20)[-1, 0] == 1
        assert donchian_vote(down, down, down, 20)[-1, 0] == -1

    def test_donchian_exits_on_half_channel(self):
        c = np.concatenate([np.linspace(100, 150, 100), np.full(10, 135.0)])[:, None].astype(np.float32)
        v = donchian_vote(c, c, c, 40)
        assert v[99, 0] == 1 and v[-1, 0] == 0          # broke the 20-bar low, not the 40-bar low

    @pytest.mark.parametrize("fn", [
        lambda c: donchian_vote(c, c, c, 30), lambda c: tsmom_vote(c, 30), lambda c: ema_vote(c, 30),
    ])
    def test_no_lookahead(self, fn):
        rng = np.random.default_rng(1)
        c = (100 * np.exp(np.cumsum(rng.normal(0, 0.01, T))))[:, None].astype(np.float32)
        base = fn(c)
        future = c.copy()
        future[300:] *= 1.5                              # rewrite the future
        assert np.array_equal(fn(future)[:300], base[:300])

    def test_nan_history_votes_zero(self):
        c = _trend(0.002)
        c[:200] = np.nan
        assert np.all(tsmom_vote(c, 30)[:230] == 0)
        assert np.all(donchian_vote(c, c, c, 30)[:230] == 0)

    def test_long_only_clips_shorts(self):
        d = _trend(-0.002)
        sig = compute_signal(SignalConfig(kind="tsmom", lookbacks_d=(1, 2), long_only=True), d, d, d)
        assert sig.min() == 0.0


class TestPortfolio:
    def test_universe_top_n_by_volume_with_history(self):
        close = np.ones((400, 3), dtype=np.float32)
        close[:300, 2] = np.nan                           # listed late -> not eligible yet
        qvol = np.tile(np.array([1.0, 2.0, 99.0], dtype=np.float32), (400, 1))
        cfg = replace(PortfolioConfig(), universe_size=1, volume_window_d=1, min_history_d=10)
        m = universe_mask(close, qvol, cfg)
        assert m[299].tolist() == [False, True, False]
        assert m[-1].tolist() == [False, False, True]     # 100 bars of history >= 60

    def test_target_weights_inverse_vol_and_gross_cap(self):
        cfg = replace(PortfolioConfig(), universe_size=2, target_vol=1.0, max_weight=1.0, max_gross=0.5)
        w = target_weights(np.array([1.0, -1.0]), np.array([0.5, 1.0]), np.array([True, True]), cfg)
        assert w[0] / -w[1] == pytest.approx(2.0)
        assert np.abs(w).sum() == pytest.approx(0.5)

    def test_out_of_universe_gets_zero(self):
        w = target_weights(np.array([1.0]), np.array([0.5]), np.array([False]), PortfolioConfig())
        assert w[0] == 0.0

    def test_band_keeps_small_changes(self):
        cur = np.array([0.10, 0.10, 0.10])
        tgt = np.array([0.11, 0.20, -0.10])
        assert apply_band(cur, tgt, 0.25).tolist() == [0.10, 0.20, -0.10]


class TestSimulate:
    def _cfg(self) -> PortfolioConfig:
        return replace(PortfolioConfig(), rebalance_every=1, rebalance_band=0.0)

    def test_pnl_fees_and_funding(self):
        close = (100 * 1.01 ** np.arange(5))[:, None]
        funding = np.full((5, 1), 0.001)
        targets = np.full((5, 1), 0.5)
        costs = CostConfig(fee=0.0005, slippage=0.0)
        res = simulate(close, funding, targets, np.arange(5) * 14400, self._cfg(), costs)
        assert res.returns[0] == pytest.approx(0.5 * 0.01 - 0.5 * 0.001 - 0.5 * 0.0005)
        assert res.fees[1] < 1e-5                         # only re-trims the drift
        assert res.funding[0] == pytest.approx(0.0005)

    def test_missing_funding_uses_baseline(self):
        close = np.full((3, 1), 100.0)
        res = simulate(close, np.full((3, 1), np.nan), np.full((3, 1), 1.0), np.arange(3),
                       self._cfg(), CostConfig(fee=0.0, slippage=0.0))
        assert res.funding[0] == pytest.approx(BASELINE_FUNDING_PER_BAR)

    def test_flattens_before_delisting(self):
        close = np.array([[100.0], [101.0], [102.0], [np.nan], [np.nan]])
        res = simulate(close, np.zeros((5, 1)), np.full((5, 1), 1.0), np.arange(5), self._cfg(),
                       CostConfig(fee=0.0, slippage=0.0))
        assert res.gross[2] == 0.0 and res.gross[1] == pytest.approx(1.0)
        assert np.all(np.isfinite(res.returns))

    def test_short_profits_in_decline(self):
        close = (100 * 0.99 ** np.arange(10))[:, None]
        res = simulate(close, np.zeros((10, 1)), np.full((10, 1), -1.0), np.arange(10), self._cfg(),
                       CostConfig(fee=0.0, slippage=0.0))
        assert np.all(res.returns > 0)
