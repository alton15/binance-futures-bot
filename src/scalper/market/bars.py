"""aggTrade -> 1-second bars, gap-filled like the backtest's reindex/ffill."""

from __future__ import annotations

from dataclasses import dataclass

from src.scalper.types import Bar

MAX_GAP_FILL_S = 3600


@dataclass
class _Acc:
    ts: int
    close: float
    high: float
    low: float
    buy: float = 0.0
    sell: float = 0.0
    has_trade: bool = False

    def to_bar(self, symbol: str) -> Bar:
        return Bar(symbol, self.ts, self.close, self.high, self.low, self.buy, self.sell)


class BarBuilder:
    """Per-symbol 1s aggregation. Emits completed bars, filling empty seconds."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._acc: _Acc | None = None

    def on_trade(self, price: float, qty: float, ts_ms: int, buyer_is_maker: bool) -> list[Bar]:
        sec = ts_ms // 1000
        out = self.flush(sec)
        acc = self._acc
        if acc is None:
            acc = self._acc = _Acc(sec, price, price, price)
        elif sec < acc.ts:
            return out  # late trade for an already-emitted second: drop
        if not acc.has_trade:
            acc.high = acc.low = price
            acc.has_trade = True
        acc.close = price
        acc.high = max(acc.high, price)
        acc.low = min(acc.low, price)
        notional = price * qty
        if buyer_is_maker:
            acc.sell += notional
        else:
            acc.buy += notional
        return out

    def flush(self, now_sec: int) -> list[Bar]:
        """Emit every bar strictly before `now_sec`; empty seconds become flat bars."""
        acc = self._acc
        if acc is None or now_sec <= acc.ts:
            return []
        out = [acc.to_bar(self.symbol)]
        for s in range(acc.ts + 1, min(now_sec, acc.ts + 1 + MAX_GAP_FILL_S)):
            out.append(Bar(self.symbol, s, acc.close, acc.close, acc.close, 0.0, 0.0))
        self._acc = _Acc(now_sec, acc.close, acc.close, acc.close)
        return out
