"""Backtest tool - simulate trading strategy on historical data."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clients.binance_rest import BinanceClient
from src.indicators.calculator import compute_indicators
from src.indicators.signals import generate_signal
from src.risk.leverage_calc import calculate_leverage, calculate_position, get_max_leverage
from config.settings import RISK, INITIAL_CAPITAL

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """A simulated backtest trade."""

    direction: str
    entry_price: float
    exit_price: float
    size: float
    leverage: int
    pnl: float
    entry_time: str
    exit_time: str
    exit_reason: str


@dataclass
class BacktestResult:
    """Backtest result summary."""

    symbol: str
    timeframe: str
    total_candles: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0
    max_drawdown: float = 0
    win_rate: float = 0
    avg_pnl: float = 0
    best_trade: float = 0
    worst_trade: float = 0
    trades: list[BacktestTrade] = field(default_factory=list)


async def run_backtest(
    symbol: str,
    from_date: str | None = None,
    to_date: str | None = None,
    timeframe: str = "1h",
    capital: float = INITIAL_CAPITAL,
) -> BacktestResult:
    """Run backtest on historical data.

    Strategy:
    - Compute indicators on rolling window
    - Generate signals
    - Simulate entries with leverage and position sizing
    - Track SL/TP exits
    """
    if "/" not in symbol:
        symbol = f"{symbol}/USDT:USDT"

    result = BacktestResult(symbol=symbol, timeframe=timeframe)

    async with BinanceClient() as client:
        # Fetch historical data
        logger.info("Fetching historical data for %s %s...", symbol, timeframe)
        since = None
        if from_date:
            since = int(datetime.strptime(from_date, "%Y-%m-%d").timestamp() * 1000)

        ohlcv = await client.fetch_ohlcv(symbol, timeframe, limit=1000)
        if not ohlcv:
            print(f"No data available for {symbol}")
            return result

        result.total_candles = len(ohlcv)
        logger.info("Got %d candles", len(ohlcv))

    # Simulation
    window_size = 250
    current_capital = capital
    peak_capital = capital
    position: dict[str, Any] | None = None

    print(f"\n{'='*60}")
    print(f"  Backtest: {symbol} ({timeframe})")
    print(f"  Candles: {len(ohlcv)} | Capital: ${capital:.2f}")
    print(f"{'='*60}\n")

    for i in range(window_size, len(ohlcv)):
        window = ohlcv[i - window_size : i]
        candle = ohlcv[i]
        ts, o, h, l, c, v = candle
        candle_time = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")

        # Check existing position exits
        if position is not None:
            exit_price = None
            exit_reason = ""

            if position["direction"] == "LONG":
                if l <= position["sl_price"]:
                    exit_price = position["sl_price"]
                    exit_reason = "stop_loss"
                elif h >= position["tp_price"]:
                    exit_price = position["tp_price"]
                    exit_reason = "take_profit"
            else:  # SHORT
                if h >= position["sl_price"]:
                    exit_price = position["sl_price"]
                    exit_reason = "stop_loss"
                elif l <= position["tp_price"]:
                    exit_price = position["tp_price"]
                    exit_reason = "take_profit"

            if exit_price:
                if position["direction"] == "LONG":
                    pnl = (exit_price - position["entry_price"]) * position["size"]
                else:
                    pnl = (position["entry_price"] - exit_price) * position["size"]

                trade = BacktestTrade(
                    direction=position["direction"],
                    entry_price=position["entry_price"],
                    exit_price=exit_price,
                    size=position["size"],
                    leverage=position["leverage"],
                    pnl=pnl,
                    entry_time=position["entry_time"],
                    exit_time=candle_time,
                    exit_reason=exit_reason,
                )
                result.trades.append(trade)

                current_capital += pnl
                peak_capital = max(peak_capital, current_capital)
                dd = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                result.max_drawdown = max(result.max_drawdown, dd)

                if pnl > 0:
                    result.wins += 1
                else:
                    result.losses += 1

                position = None
                continue

        # No position - check for entry
        if position is None:
            indicators = compute_indicators(window, symbol, timeframe)
            if indicators is None:
                continue

            signal = generate_signal(indicators)

            if signal.is_actionable and indicators.atr and indicators.atr > 0:
                # Calculate position
                volatility = (h - l) / c if c > 0 else 0.03
                leverage = calculate_leverage(volatility, signal.strength)
                params = calculate_position(
                    entry_price=c,
                    atr=indicators.atr,
                    direction=signal.direction,
                    leverage=leverage,
                    capital=current_capital,
                )

                if params.position_size > 0 and params.margin_required <= current_capital:
                    position = {
                        "direction": signal.direction,
                        "entry_price": c,
                        "size": params.position_size,
                        "leverage": leverage,
                        "sl_price": params.sl_price,
                        "tp_price": params.tp_price,
                        "entry_time": candle_time,
                    }

    # Close any remaining position at last candle
    if position is not None:
        last_close = ohlcv[-1][4]
        if position["direction"] == "LONG":
            pnl = (last_close - position["entry_price"]) * position["size"]
        else:
            pnl = (position["entry_price"] - last_close) * position["size"]

        trade = BacktestTrade(
            direction=position["direction"],
            entry_price=position["entry_price"],
            exit_price=last_close,
            size=position["size"],
            leverage=position["leverage"],
            pnl=pnl,
            entry_time=position["entry_time"],
            exit_time="end",
            exit_reason="backtest_end",
        )
        result.trades.append(trade)
        current_capital += pnl

    # Summary
    result.total_trades = len(result.trades)
    result.total_pnl = current_capital - capital
    result.win_rate = result.wins / result.total_trades if result.total_trades > 0 else 0
    pnls = [t.pnl for t in result.trades]
    result.avg_pnl = sum(pnls) / len(pnls) if pnls else 0
    result.best_trade = max(pnls) if pnls else 0
    result.worst_trade = min(pnls) if pnls else 0

    # Print results
    print(f"  Total Trades:  {result.total_trades}")
    print(f"  Wins:          {result.wins}")
    print(f"  Losses:        {result.losses}")
    print(f"  Win Rate:      {result.win_rate:.1%}")
    print(f"  Total P&L:     ${result.total_pnl:.4f}")
    print(f"  Best Trade:    ${result.best_trade:.4f}")
    print(f"  Worst Trade:   ${result.worst_trade:.4f}")
    print(f"  Avg P&L:       ${result.avg_pnl:.4f}")
    print(f"  Max Drawdown:  {result.max_drawdown:.1%}")
    print(f"  Final Capital: ${current_capital:.4f}")

    if result.trades:
        print(f"\n  Trade History:")
        for i, t in enumerate(result.trades, 1):
            sign = "+" if t.pnl >= 0 else ""
            print(f"    {i:3d}. {t.direction:5s} {t.leverage}x @ {t.entry_price:.2f} -> "
                  f"{t.exit_price:.2f} | {sign}${t.pnl:.4f} ({t.exit_reason})")

    print()
    return result


if __name__ == "__main__":
    import asyncio
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    asyncio.run(run_backtest(symbol))
