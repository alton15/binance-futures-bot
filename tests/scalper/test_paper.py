"""Paper broker fill rules and storage idempotency."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.scalper.config import MAKER_FEE, TAKER_FEE, StrategyConfig
from src.scalper.execution.paper import PaperBroker
from src.scalper.risk.sizing import STOP_SLIPPAGE, SizeResult
from src.scalper.storage import Storage, position_meta
from src.scalper.types import ExitReason, Quote, Side, Signal

SYM = "XYZ/USDT:USDT"
SIZE = SizeResult(qty=10.0, notional=1000.0, leverage=5, margin=200.0, risk_usdt=7.0)


def _signal(side: Side = Side.LONG, price: float = 100.0, move_bps: float = 100.0) -> Signal:
    d = int(side)
    return Signal(SYM, side, ts=0, price=price, move_bps=d * move_bps, z=5.0,
                  tp_price=price * (1 + d * 0.01), sl_price=price * (1 - d * 0.005))


def _broker(**over) -> PaperBroker:
    return PaperBroker(replace(StrategyConfig(), **over), latency_s=1.0)


def test_taker_entry_fills_at_ask_after_latency():
    b = _broker(maker_entry=False)
    b.on_quote(Quote(SYM, 99.99, 100.01, 0))
    assert b.submit_entry(_signal(), SIZE, now=0.0) == ""
    assert b.on_trade(SYM, 100.0, now=0.5) == (None, None)       # still in latency window
    opened, _ = b.on_trade(SYM, 100.0, now=1.0)
    assert opened is not None
    assert opened.entry_price == pytest.approx(100.01)
    assert opened.entry_fee == pytest.approx(100.01 * 10 * TAKER_FEE)
    assert opened.tp_price == pytest.approx(100.01 * 1.01)        # tp_frac=1.0 x 100bps
    assert opened.sl_price == pytest.approx(100.01 * 0.995)       # sl_frac=0.5 x 100bps


def test_maker_entry_rejected_if_post_only_would_cross():
    b = _broker(maker_entry=True)
    b.on_quote(Quote(SYM, 99.9, 100.0, 0))                        # ask at limit -> crosses
    assert b.submit_entry(_signal(), SIZE, now=0.0) == "post-only would cross"


def test_maker_entry_needs_trade_through_and_expires():
    b = _broker(maker_entry=True, maker_wait_s=5)
    b.on_quote(Quote(SYM, 99.9, 100.1, 0))
    b.submit_entry(_signal(), SIZE, now=0.0)
    assert b.on_trade(SYM, 100.0, now=2.0)[0] is None              # touch is not enough
    expired, _ = b.on_clock(now=6.0)
    assert expired == [SYM] and SYM not in b.pending


def test_maker_entry_fill_uses_limit_and_maker_fee():
    b = _broker(maker_entry=True)
    b.on_quote(Quote(SYM, 99.9, 100.1, 0))
    b.submit_entry(_signal(), SIZE, now=0.0)
    opened, _ = b.on_trade(SYM, 99.99, now=2.0)
    assert opened.entry_price == 100.0
    assert opened.entry_fee == pytest.approx(100.0 * 10 * MAKER_FEE)


def _open_long(b: PaperBroker):
    b.on_quote(Quote(SYM, 100.0, 100.0, 0))
    b.submit_entry(_signal(), SIZE, now=0.0)
    opened, _ = b.on_trade(SYM, 100.0, now=1.0)
    return opened


def test_stop_loss_fills_worse_of_stop_and_book_with_slippage():
    b = _broker()
    pos = _open_long(b)
    b.on_quote(Quote(SYM, 99.0, 99.1, 0))                         # book gapped below stop
    _, closed = b.on_trade(SYM, 99.2, now=2.0)
    assert closed.reason is ExitReason.STOP_LOSS
    assert closed.exit_price == pytest.approx(99.0 * (1 - STOP_SLIPPAGE))
    assert closed.net_pnl < -(pos.entry_price - pos.sl_price) * pos.qty


def test_take_profit_requires_trade_through():
    b = _broker()
    pos = _open_long(b)
    assert b.on_trade(SYM, pos.tp_price, now=2.0)[1] is None
    _, closed = b.on_trade(SYM, pos.tp_price + 0.01, now=3.0)
    assert closed.reason is ExitReason.TAKE_PROFIT
    assert closed.exit_price == pos.tp_price
    assert closed.exit_fee == pytest.approx(pos.tp_price * pos.qty * MAKER_FEE)


def test_time_stop_closes_at_book():
    b = _broker(max_hold_s=900)
    _open_long(b)
    b.on_quote(Quote(SYM, 100.2, 100.3, 900_000))
    _, closes = b.on_clock(now=901.0)
    assert len(closes) == 1 and closes[0].reason is ExitReason.TIME_STOP
    assert closes[0].exit_price == 100.2 and not b.positions


def test_time_stop_ignores_stale_quote_and_uses_last_trade():
    b = _broker(max_hold_s=900)
    _open_long(b)                                                # quote ts=0, last trade 100.0
    b.on_trade(SYM, 100.7, now=500.0)
    _, closes = b.on_clock(now=901.0)
    assert closes[0].exit_price == 100.7                          # not the 900s-old book


def test_restored_position_without_any_price_defers_time_stop():
    src = _broker()
    pos = _open_long(src)
    b = _broker(max_hold_s=900)
    b.restore(pos)
    _, closes = b.on_clock(now=5_000.0)
    assert closes == [] and SYM in b.positions                    # never fabricate an exit price
    b.on_trade(SYM, 99.8, now=5_001.0)                           # inside the bracket
    _, closes = b.on_clock(now=5_002.0)
    assert closes[0].exit_price == 99.8


def test_maker_entry_cannot_fill_after_expiry():
    b = _broker(maker_entry=True, maker_wait_s=5)
    b.on_quote(Quote(SYM, 99.9, 100.1, 0))
    b.submit_entry(_signal(), SIZE, now=0.0)
    assert b.on_trade(SYM, 99.0, now=6.5)[0] is None             # window closed at 6.0


def test_short_round_trip_pnl_sign():
    b = _broker()
    b.on_quote(Quote(SYM, 100.0, 100.0, 0))
    b.submit_entry(_signal(Side.SHORT), SIZE, now=0.0)
    pos, _ = b.on_trade(SYM, 100.0, now=1.0)
    _, closed = b.on_trade(SYM, pos.tp_price - 0.01, now=2.0)
    assert closed.reason is ExitReason.TAKE_PROFIT and closed.net_pnl > 0


async def test_storage_close_is_idempotent_and_restores(tmp_path):
    db = Storage(tmp_path / "t.db", "paper")
    await db.open()
    try:
        b = _broker()
        pos = _open_long(b)
        db_id = await db.open_position(pos, position_meta(_signal()))
        restored = await db.open_positions()
        assert [p.symbol for _, p in restored] == [SYM]
        _, closed = b.on_trade(SYM, pos.tp_price + 0.01, now=2.0)
        assert await db.close_position(db_id, closed) is True
        assert await db.close_position(db_id, closed) is False      # double close prevented
        assert await db.realized_pnl() == pytest.approx(closed.net_pnl)
        st = await db.stats()
        assert st["trades"] == 1 and st["win_rate"] == 1.0
        assert await db.open_positions() == []
    finally:
        await db.close()
