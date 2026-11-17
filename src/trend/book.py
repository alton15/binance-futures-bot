"""Immutable paper account: cash + signed holdings, with pure update functions."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Mapping

QTY_EPS = 1e-12


@dataclass(frozen=True)
class Holding:
    qty: float            # signed contracts (base units)
    entry_price: float    # average entry of the open quantity


@dataclass(frozen=True)
class Book:
    cash: float                                   # realized balance (fees, funding, closed PnL)
    holdings: Mapping[str, Holding] = field(default_factory=lambda: MappingProxyType({}))

    def unrealized(self, marks: Mapping[str, float]) -> float:
        return sum(h.qty * (marks[s] - h.entry_price) for s, h in self.holdings.items() if s in marks)

    def equity(self, marks: Mapping[str, float]) -> float:
        return self.cash + self.unrealized(marks)

    def weights(self, marks: Mapping[str, float]) -> dict[str, float]:
        eq = self.equity(marks)
        if eq <= 0:
            return {s: 0.0 for s in self.holdings}
        return {s: h.qty * marks[s] / eq for s, h in self.holdings.items() if s in marks}

    def gross(self, marks: Mapping[str, float]) -> float:
        return sum(abs(w) for w in self.weights(marks).values())


def apply_fill(book: Book, symbol: str, dqty: float, price: float, fee: float) -> tuple[Book, float]:
    """Return (new book, realized PnL of this fill excluding the fee)."""
    cur = book.holdings.get(symbol, Holding(0.0, 0.0))
    new_qty = cur.qty + dqty
    realized = 0.0
    if cur.qty != 0 and (cur.qty > 0) != (dqty > 0):           # reducing or flipping
        closed = min(abs(dqty), abs(cur.qty))
        realized = closed * (price - cur.entry_price) * (1 if cur.qty > 0 else -1)
    if abs(new_qty) < QTY_EPS:
        entry = 0.0
    elif cur.qty == 0 or (cur.qty > 0) != (new_qty > 0):      # fresh position or flipped
        entry = price
    elif abs(new_qty) > abs(cur.qty):                           # adding
        entry = (cur.qty * cur.entry_price + dqty * price) / new_qty
    else:                                                       # partial reduce keeps entry
        entry = cur.entry_price
    holdings = dict(book.holdings)
    if abs(new_qty) < QTY_EPS:
        holdings.pop(symbol, None)
    else:
        holdings[symbol] = Holding(new_qty, entry)
    return replace(book, cash=book.cash + realized - fee, holdings=MappingProxyType(holdings)), realized


def apply_cash(book: Book, amount: float) -> Book:
    """Funding and other cash adjustments (positive = credit)."""
    return replace(book, cash=book.cash + amount)
