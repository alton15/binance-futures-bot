"""Immutable domain types shared by strategy, execution and backtest."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(int, Enum):
    LONG = 1
    SHORT = -1


class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIME_STOP = "time_stop"
    KILL_SWITCH = "kill_switch"
    MANUAL = "manual"


@dataclass(frozen=True)
class Bar:
    """One-second bar built from aggTrades."""

    symbol: str
    ts: int            # epoch seconds (bar open)
    close: float
    high: float
    low: float
    buy_notional: float
    sell_notional: float


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    ts_ms: int


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: Side
    ts: int
    price: float       # reference price at signal time
    move_bps: float    # signed move that triggered the fade
    z: float
    tp_price: float
    sl_price: float


@dataclass(frozen=True)
class Position:
    id: int
    symbol: str
    side: Side
    qty: float
    entry_price: float
    tp_price: float
    sl_price: float
    opened_ts: float
    entry_fee: float
    leverage: int

    def pnl_at(self, price: float) -> float:
        return int(self.side) * (price - self.entry_price) * self.qty


@dataclass(frozen=True)
class ClosedTrade:
    position: Position
    exit_price: float
    exit_fee: float
    closed_ts: float
    reason: ExitReason

    @property
    def gross_pnl(self) -> float:
        return self.position.pnl_at(self.exit_price)

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.position.entry_fee - self.exit_fee
