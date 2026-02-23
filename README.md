# Binance Futures Trading Bot

Technical analysis based Binance USDT-M futures automated trading bot.

## Features

- **Technical Analysis**: RSI, MACD, Bollinger Bands, EMA (9/21/200), ATR, ADX, Stochastic
- **Dynamic Coin Scanning**: Auto-selects coins by volume/volatility
- **Dynamic Leverage**: 2-8x based on volatility tier, signal strength, and drawdown
- **10-Gate Risk Management**: Signal strength, position limits, daily loss, drawdown, margin, exposure, leverage, liquidation buffer, funding rate
- **Paper / Live Mode**: Switch via `TRADING_MODE` env variable
- **Multi-Timeframe**: Primary 1h with 15m/4h confirmation
- **Position Monitoring**: SL/TP/trailing stop/liquidation/funding/max hold time
- **Discord Alerts**: Trade execution and daily report notifications
- **Backtesting**: Historical data backtest tool
- **Docker**: Ready for server deployment

## Quick Start

```bash
# Clone
git clone https://github.com/alton15/binance-futures-bot.git
cd binance-futures-bot

# Setup
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run
futuresbot run --paper           # Single pipeline run (paper mode)
futuresbot run --paper --dry-run # Scan/analyze only, no trades
futuresbot run --paper --loop    # Start scheduler daemon
futuresbot scan                  # Scan coins only
futuresbot analyze BTCUSDT       # Analyze specific symbol
futuresbot status                # Show bot status
futuresbot positions             # Show open positions
futuresbot history               # Show trade history
futuresbot backtest BTCUSDT      # Run backtest
```

## Docker Deployment

```bash
cp .env.example .env  # Configure API keys
docker compose up -d
docker compose logs -f
```

## Architecture

```
Scan -> Analyze -> Risk Check -> Execute
 |        |           |            |
 |   Indicators    10-Gate     Paper/Live
 |   + Signals    Validator     Trader
 |
CoinScanner
(volume/volatility)
```

## Risk Management (10 Gates)

| Gate | Check | Threshold |
|------|-------|-----------|
| 1 | Signal Strength | >= 0.6 |
| 2 | Max Open Positions | 5 |
| 3 | No Duplicate Symbol | - |
| 4 | Daily Loss Limit | 5% of capital |
| 5 | Max Drawdown | 15% from peak |
| 6 | Available Margin | Sufficient for trade |
| 7 | Total Exposure | 50% of capital |
| 8 | Leverage Validation | 2-8x range |
| 9 | Liquidation Buffer | 30%+ distance |
| 10 | Funding Rate | < 0.1% per 8h |

## Leverage Tiers

| Daily Volatility | Max Leverage |
|-----------------|-------------|
| 0-2% | 8x |
| 2-4% | 5x |
| 4-6% | 3x |
| 6%+ | 2x |

## Testing

```bash
pytest tests/ -v
```

## Tech Stack

- Python 3.11+, async
- ccxt (Binance Futures API)
- pandas-ta (technical indicators)
- aiosqlite (async database)
- APScheduler (job scheduling)
- websockets (real-time streams)
- httpx (HTTP client)
