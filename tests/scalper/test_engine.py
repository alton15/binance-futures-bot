"""Engine wiring: synthetic market data -> signal -> paper fill -> close -> DB."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np
import pytest

from src.scalper import engine as engine_mod
from src.scalper.config import Settings, StrategyConfig
from src.scalper.engine import Engine
from src.scalper.notify import Notifier
from src.scalper.storage import Storage

SYM = "XYZ/USDT:USDT"
WS = "XYZUSDT"
T0 = 1_800_000_000


@dataclass(frozen=True)
class _Spec:
    symbol: str
    ws_id: str
    qty_step: float = 0.001
    tick_size: float = 0.0001
    min_notional: float = 5.0


class FakeExchange:
    async def load_markets(self) -> dict:
        return {SYM: _Spec(SYM, WS.lower())}

    def spec(self, symbol: str) -> _Spec:
        return _Spec(SYM, WS.lower())

    def has_spec(self, symbol: str) -> bool:
        return symbol == SYM

    def symbol_for_ws(self, ws_id: str) -> str | None:
        return SYM if ws_id.upper() == WS else None

    async def top_symbols(self, *_: object) -> list[str]:
        return [SYM]


class FakeClock:
    def __init__(self) -> None:
        self.now = float(T0)

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now


def _settings(tmp_path) -> Settings:
    strat = StrategyConfig(follow=True, lookback_s=10, sigma_window_s=60, z_entry=3.0, min_move_bps=20.0,
                           tp_frac=1.0, sl_frac=0.5, max_hold_s=600, cooldown_s=60)
    return Settings(api_key="", api_secret="", mode="paper", testnet=False, initial_capital=700.0,
                    discord_alerts="", discord_reports="", db_path=tmp_path / "e.db", strategy=strat)


def _trade(engine: Engine, clock: FakeClock, price: float, ts: float) -> None:
    clock.now = ts
    engine._on_public("", {"s": WS, "b": price * 0.99995, "a": price * 1.00005, "T": int(ts * 1000)})
    engine._on_market("", {"e": "aggTrade", "s": WS, "p": str(price), "q": "1", "T": int(ts * 1000), "m": False})


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(engine_mod, "time", clock)
    s = _settings(tmp_path)
    db = Storage(s.db_path, "paper")
    await db.open()
    notifier = Notifier("", "", "test")
    e = Engine(s, FakeExchange(), db, notifier)
    e._guard = engine_mod.RiskGuard(s.risk, 700.0, None, now=clock.now)
    await e._refresh_universe()
    yield e, clock, db
    await notifier.close()
    await db.close()


async def test_breakout_opens_and_take_profit_closes(engine):
    e, clock, db = engine
    rng = np.random.default_rng(7)
    price = 100.0
    for i in range(120):                                  # quiet warmup, 1 trade/s
        price *= 1 + rng.normal(0, 2e-4)
        _trade(e, clock, price, T0 + i)
    for i in range(120, 126):                             # +~3% breakout
        price *= 1.005
        _trade(e, clock, price, T0 + i)
    await asyncio.sleep(0.1)                               # let persistence tasks run
    assert not e._broker.positions                         # breakout continued through TP
    rows = await db.db.execute_fetchall("SELECT side, reason, net_pnl FROM positions WHERE status='closed'")
    assert len(rows) == 1
    side, reason, net = rows[0]
    assert side == 1 and reason == "take_profit" and net > 0
    assert e._guard.equity == pytest.approx(700.0 + net)
    outcomes = [r[0] for r in await db.db.execute_fetchall("SELECT outcome FROM signals ORDER BY id")]
    assert outcomes[0].startswith("submitted")
    assert len(outcomes) == 1                              # repeat signals deduped (busy/cooldown)
    snaps = await db.db.execute_fetchall("SELECT equity, peak FROM equity")
    assert snaps and snaps[-1][0] == pytest.approx(700.0 + net)   # peak persisted on close


async def test_reconnect_blocks_entries(engine):
    e, clock, _ = engine
    e._entry_block_until = T0 + 10_000
    rng = np.random.default_rng(7)
    price = 100.0
    for i in range(120):
        price *= 1 + rng.normal(0, 2e-4)
        _trade(e, clock, price, T0 + i)
    for i in range(120, 126):
        price *= 1.005
        _trade(e, clock, price, T0 + i)
    await asyncio.sleep(0.05)
    assert not e._broker.positions and not e._broker.pending


def test_live_mode_refused(tmp_path):
    s = _settings(tmp_path)
    live = Settings(**{**s.__dict__, "mode": "live"})
    with pytest.raises(NotImplementedError):
        Engine(live, FakeExchange(), Storage(s.db_path, "live"), Notifier("", "", "t"))
