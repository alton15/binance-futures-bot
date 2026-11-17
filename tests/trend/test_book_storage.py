"""Immutable paper book arithmetic and its SQLite persistence."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from src.trend.book import Book, Holding, apply_cash, apply_fill
from src.trend.storage import TrendStorage


def test_open_add_reduce_close_long():
    b0 = Book(1000.0)
    b1, r1 = apply_fill(b0, "X", 2.0, 100.0, 0.1)
    b2, r2 = apply_fill(b1, "X", 2.0, 110.0, 0.1)
    assert b2.holdings["X"] == Holding(4.0, 105.0)
    b3, r3 = apply_fill(b2, "X", -1.0, 120.0, 0.1)
    assert r3 == pytest.approx(15.0) and b3.holdings["X"].entry_price == 105.0
    b4, r4 = apply_fill(b3, "X", -3.0, 100.0, 0.1)
    assert "X" not in b4.holdings and r4 == pytest.approx(-15.0)
    assert b4.cash == pytest.approx(1000.0 - 0.4)
    assert b0.holdings == {} and b1.holdings["X"].qty == 2.0          # inputs never mutated
    assert (r1, r2) == (0.0, 0.0)


def test_flip_short_to_long_realizes_and_resets_entry():
    b, _ = apply_fill(Book(1000.0), "X", -2.0, 100.0, 0.0)
    b, r = apply_fill(b, "X", 3.0, 90.0, 0.0)
    assert r == pytest.approx(20.0)
    assert b.holdings["X"] == Holding(1.0, 90.0)


def test_equity_weights_and_cash():
    b = Book(500.0, MappingProxyType({"X": Holding(2.0, 100.0), "Y": Holding(-1.0, 50.0)}))
    marks = {"X": 110.0, "Y": 40.0}
    assert b.equity(marks) == pytest.approx(500.0 + 20.0 + 10.0)
    assert b.weights(marks)["Y"] == pytest.approx(-40.0 / 530.0)
    assert b.gross(marks) == pytest.approx(260.0 / 530.0)
    assert apply_cash(b, -3.0).cash == 497.0 and b.cash == 500.0


async def test_storage_roundtrip_and_idempotent_funding(tmp_path):
    db = TrendStorage(tmp_path / "s.db")
    await db.open()
    try:
        assert (await db.load_book(700.0)).cash == 700.0
        book = Book(650.5, MappingProxyType({"X": Holding(-1.5, 20.0)}))
        await db.save_book(book)
        assert await db.load_book(700.0) == book
        await db.save_book(Book(651.0))
        assert (await db.load_book(700.0)).holdings == {}
        assert await db.record_funding(1.0, "X", 0.0001, -0.01) is True
        assert await db.record_funding(1.0, "X", 0.0001, -0.01) is False
        await db.snapshot_equity(1.0, 700.0, 700.0, 0.0, 0, 710.0)
        await db.snapshot_equity(2.0, 690.0, 690.0, 0.0, 0, 690.0)
        assert await db.peak_equity() == 690.0                       # latest (reset) peak wins
    finally:
        await db.close()
