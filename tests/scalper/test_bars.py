from src.scalper.market.bars import BarBuilder

T = 1_800_000_000


def test_aggregates_within_second_and_splits_buy_sell():
    b = BarBuilder("X")
    assert b.on_trade(10.0, 1, T * 1000 + 100, False) == []
    assert b.on_trade(11.0, 2, T * 1000 + 500, True) == []
    assert b.on_trade(9.5, 1, T * 1000 + 900, False) == []
    bars = b.on_trade(9.7, 1, (T + 1) * 1000, False)
    assert len(bars) == 1
    bar = bars[0]
    assert (bar.ts, bar.close, bar.high, bar.low) == (T, 9.5, 11.0, 9.5)
    assert bar.buy_notional == 10.0 + 9.5
    assert bar.sell_notional == 22.0


def test_gap_seconds_are_flat_bars():
    b = BarBuilder("X")
    b.on_trade(10.0, 1, T * 1000, False)
    bars = b.on_trade(12.0, 1, (T + 3) * 1000, False)
    assert [x.ts for x in bars] == [T, T + 1, T + 2]
    assert all(x.close == 10.0 and x.buy_notional == 0 for x in bars[1:])


def test_new_second_high_low_exclude_previous_close():
    b = BarBuilder("X")
    b.on_trade(10.0, 1, T * 1000, False)
    b.flush(T + 1)                      # clock tick, no trades yet in T+1
    b.on_trade(12.0, 1, (T + 1) * 1000 + 10, False)
    bar = b.flush(T + 2)[0]
    assert (bar.ts, bar.high, bar.low, bar.close) == (T + 1, 12.0, 12.0, 12.0)


def test_flush_emits_flat_bar_when_no_trades():
    b = BarBuilder("X")
    b.on_trade(10.0, 1, T * 1000, False)
    b.flush(T + 1)
    bars = b.flush(T + 2)
    assert [(x.ts, x.close, x.high, x.low) for x in bars] == [(T + 1, 10.0, 10.0, 10.0)]


def test_late_trade_dropped():
    b = BarBuilder("X")
    b.on_trade(10.0, 1, (T + 5) * 1000, False)
    assert b.on_trade(99.0, 1, T * 1000, False) == []
    assert b.flush(T + 6)[0].high == 10.0
