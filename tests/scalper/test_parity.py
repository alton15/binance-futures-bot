"""Live incremental features and signals must match the vectorized backtest."""

import numpy as np
import pandas as pd
import pytest

from src.scalper.backtest.simulator import build_features, candidate_mask
from src.scalper.config import StrategyConfig
from src.scalper.strategy.fade import evaluate
from src.scalper.strategy.features import SymbolFeatures
from src.scalper.types import Bar

LOOKBACK = 60
WINDOW = 600


@pytest.fixture
def bars() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = 3000
    rets = rng.standard_t(3, n) * 2e-4
    rets[1500:1530] -= 4e-3          # inject a sharp dump to trigger signals
    close = 100 * np.exp(np.cumsum(rets))
    vol = rng.exponential(1000, n)
    idx = pd.RangeIndex(1_700_000_000, 1_700_000_000 + n)
    return pd.DataFrame({"close": close, "high": close, "low": close,
                         "buy": vol * 0.5, "sell": vol * 0.5}, index=idx)


def _incremental(bars: pd.DataFrame):
    feat = SymbolFeatures(LOOKBACK, WINDOW)
    snaps = {}
    for ts, row in bars.iterrows():
        bar = Bar("X", int(ts), row.close, row.high, row.low, row.buy, row.sell)
        s = feat.push(bar)
        if s is not None:
            snaps[int(ts)] = s
    return snaps


def test_features_match_vectorized(bars):
    vec = build_features(bars, LOOKBACK, WINDOW)
    inc = _incremental(bars)
    checked = 0
    for i, ts in enumerate(vec.sec):
        if not np.isfinite(vec.z[i]):
            assert int(ts) not in inc
            continue
        s = inc[int(ts)]
        assert s.move_bps == pytest.approx(vec.move_bps[i], rel=1e-9, abs=1e-9)
        assert s.z == pytest.approx(vec.z[i], rel=1e-6, abs=1e-9)
        assert s.burst == pytest.approx(vec.burst[i], rel=1e-6, abs=1e-9)
        checked += 1
    assert checked > 2000


def test_signals_match_vectorized(bars):
    cfg = StrategyConfig(follow=False, lookback_s=LOOKBACK, sigma_window_s=WINDOW, z_entry=3.0, min_move_bps=20)
    vec = build_features(bars, LOOKBACK, WINDOW)
    mask = candidate_mask(vec, cfg)
    vec_ts = set(int(t) for t in vec.sec[mask])
    inc_ts = {ts for ts, s in _incremental(bars).items() if evaluate("X", s, cfg) is not None}
    assert vec_ts, "fixture should produce signals"
    assert vec_ts == inc_ts


def test_fade_direction_is_against_move(bars):
    cfg = StrategyConfig(follow=False, lookback_s=LOOKBACK, sigma_window_s=WINDOW, z_entry=3.0, min_move_bps=20)
    sigs = [evaluate("X", s, cfg) for s in _incremental(bars).values()]
    sigs = [s for s in sigs if s is not None]
    for s in sigs:
        assert int(s.side) == -int(np.sign(s.move_bps))
        if int(s.side) > 0:
            assert s.sl_price < s.price < s.tp_price
        else:
            assert s.tp_price < s.price < s.sl_price
