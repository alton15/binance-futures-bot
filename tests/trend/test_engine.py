"""Paper engine: data loading, backtest parity, funding idempotency, regime filter, drawdown halt."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from src.trend.config import BAR_S, TrendRiskConfig, TrendSettings
from src.trend.engine import TrendEngine, kline_limit
from src.trend.portfolio import realized_vol, target_weights, universe_mask
from src.trend.profiles import get_profile
from src.trend.signals import compute_signal
from src.trend.storage import TrendStorage
from tests.trend.fakes import FUNDING_RATE, FakeClock, NullNotifier, build_exchange

LAST_CLOSED = (1_800_000_000 // (6 * BAR_S)) * 6 * BAR_S          # a daily-rebalance bar
SYMBOLS = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT")
UP = (0.004, 0.005, 0.006, -0.004, 0.003)
DOWN_BTC = (-0.006, 0.005, 0.006, -0.004, 0.003)


def _settings(tmp_path: Path, profile: str, **risk) -> TrendSettings:
    prof = get_profile(profile)
    prof = replace(prof, portfolio=replace(prof.portfolio, universe_size=4))
    return TrendSettings(api_key="", api_secret="", mode="paper", initial_capital=700.0, discord_alerts="",
                         discord_reports="", db_path=tmp_path / "t.db", profile=prof,
                         risk=replace(TrendRiskConfig(), **risk))


async def _engine(tmp_path: Path, profile: str, drifts=UP, **risk):
    settings = _settings(tmp_path, profile, **risk)
    ex = build_exchange(SYMBOLS, drifts, LAST_CLOSED)
    db = TrendStorage(settings.db_path)
    await db.open()
    clock = FakeClock(LAST_CLOSED + BAR_S + 30)
    eng = TrendEngine(settings, ex, db, NullNotifier(), clock=clock)
    return eng, ex, db, clock, settings


def _expected_weights(settings: TrendSettings, ex) -> dict[str, float]:
    """Backtest code path on the same closed bars the engine should have loaded."""
    limit = kline_limit(settings)
    sl = slice(-1 - limit, -1)                                      # drop the still-open bar
    close = np.column_stack([ex.closes[s][sl] for s in SYMBOLS]).astype(np.float32)
    qvol = np.column_stack([np.full(limit, ex.qvol[s] / 6) for s in SYMBOLS]).astype(np.float32)
    p = settings.profile
    sig = compute_signal(p.signal, close, close * 1.005, close * 0.995, regime_col=0)
    vol = realized_vol(close, p.portfolio.vol_window_d * 6)
    uni = universe_mask(close, qvol, p.portfolio)
    w = target_weights(sig[-1], vol[-1], uni[-1], p.portfolio)
    return dict(zip(SYMBOLS, w, strict=True))


async def test_live_mode_refused(tmp_path):
    settings = replace(_settings(tmp_path, "trend_lo"), mode="live")
    with pytest.raises(NotImplementedError):
        TrendEngine(settings, None, None, NullNotifier())


@pytest.mark.parametrize("profile", ["trend_lo", "trend_ls"])
async def test_first_step_matches_backtest_targets(tmp_path, profile):
    eng, ex, db, _, settings = await _engine(tmp_path, profile)
    try:
        await eng.step(LAST_CLOSED, force_rebalance=True)
        rows = await db.db.execute_fetchall("SELECT symbol, weight FROM targets WHERE ts=?", (LAST_CLOSED,))
        got = {s: w for s, w in rows}
        for sym, w in _expected_weights(settings, ex).items():
            assert got.get(sym, 0.0) == pytest.approx(w, abs=1e-6), sym
        fills = await db.db.execute_fetchall("SELECT symbol FROM fills")
        assert fills, "forced first rebalance should open positions"
        if profile == "trend_lo":
            assert all(h.qty > 0 for h in eng._book.holdings.values())
        else:
            assert any(h.qty < 0 for h in eng._book.holdings.values()), "down-trending XRP is shorted"
    finally:
        await db.close()


async def test_regime_filter_blocks_longs_when_btc_down(tmp_path):
    eng, _, db, _, _ = await _engine(tmp_path, "trend_lo", drifts=DOWN_BTC)
    try:
        await eng.step(LAST_CLOSED, force_rebalance=True)
        assert not eng._book.holdings
    finally:
        await db.close()


async def test_step_is_idempotent_and_funding_booked_once(tmp_path):
    eng, ex, db, clock, _ = await _engine(tmp_path, "trend_lo")
    try:
        await eng.step(LAST_CLOSED, force_rebalance=True)
        held = dict(eng._book.holdings)
        nxt = LAST_CLOSED + BAR_S
        ex.funding_ts = [nxt + BAR_S]                                # settles at the end of the next bar
        clock.now = nxt + BAR_S + 30
        await eng.step(nxt)
        n_fund = await db.db.execute_fetchall("SELECT COUNT(*), SUM(amount) FROM funding")
        assert n_fund[0][0] == len(held)
        cash_after = eng._book.cash
        await eng.step(nxt)                                          # already done -> no-op
        assert eng._book.cash == cash_after
        eng._book = await db.load_book(700.0)
        await eng._book_funding(nxt + BAR_S, {})                     # re-fetch the same settlement
        assert eng._book.cash == pytest.approx(cash_after)
        expected = -sum(h.qty * FUNDING_RATE * ex.closes[s][-1] for s, h in held.items())
        assert n_fund[0][1] == pytest.approx(expected, rel=1e-6)
    finally:
        await db.close()


async def test_restart_restores_book(tmp_path):
    eng, ex, db, clock, settings = await _engine(tmp_path, "trend_lo")
    try:
        await eng.step(LAST_CLOSED, force_rebalance=True)
        before = (eng._book.cash, dict(eng._book.holdings))
        eng2 = TrendEngine(settings, ex, db, NullNotifier(), clock=clock)
        await eng2._restore()
        assert (eng2._book.cash, dict(eng2._book.holdings)) == before
    finally:
        await db.close()


async def test_drawdown_flattens_and_halts(tmp_path):
    eng, ex, db, clock, _ = await _engine(tmp_path, "trend_lo", max_drawdown=0.05)
    try:
        await eng.step(LAST_CLOSED, force_rebalance=True)
        assert eng._book.holdings
        ex.shock = {s: 0.5 for s in SYMBOLS}                         # everything halves
        clock.now += BAR_S
        await eng.step(LAST_CLOSED + BAR_S)
        assert not eng._book.holdings
        assert await db.get_meta("halted") == "1"
        assert any("HALT" in a for a in eng._notify.alerts)
    finally:
        await db.close()
