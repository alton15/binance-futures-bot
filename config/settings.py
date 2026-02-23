"""Global settings - trading config, risk parameters, indicators, schedules."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# -- Paths ---------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "futures_bot.db"

# -- Binance API ---------------------------------------------------
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

# -- Trading Mode --------------------------------------------------
TRADING_MODE = os.getenv("TRADING_MODE", "paper")  # paper or live

# -- Initial Capital -----------------------------------------------
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100"))

# -- Market Settings -----------------------------------------------
MARKET = {
    "quote_currency": "USDT",
    "default_timeframe": "1h",
    "confirm_timeframes": ["15m", "4h"],
    "max_coins_per_scan": 10,
    "exclude_symbols": ["BTCDOMUSDT", "DEFIUSDT"],
}

# -- Technical Indicator Parameters --------------------------------
INDICATORS = {
    "rsi_period": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_std": 2.0,
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 200,
    "atr_period": 14,
    "adx_period": 14,
    "stoch_k": 14,
    "stoch_d": 3,
}

# -- Signal Generation ---------------------------------------------
SIGNALS = {
    "weights": {
        "macd": 2.0,
        "rsi": 1.5,
        "ema_trend": 1.5,
        "bollinger": 1.0,
        "ema_cross": 1.0,
        "stochastic": 1.0,
        "volume": 1.0,
        "adx": 0.5,
    },
    "min_confirming": 3,
    "min_strength": 0.6,
}

# -- Risk Parameters -----------------------------------------------
RISK = {
    "max_open_positions": 7,
    "risk_per_trade_pct": 0.03,       # 3% of capital per trade
    "daily_loss_limit_pct": 0.08,     # 8% daily loss limit
    "max_drawdown_pct": 0.25,         # 25% max drawdown from peak
    "max_exposure_pct": 0.70,         # 70% total capital exposure
    "signal_strength_min": 0.6,
    "liquidation_buffer_pct": 0.30,   # 30% distance to liquidation
    "funding_rate_max": 0.001,        # 0.1% per 8h
    "min_volume_24h": 50_000_000,     # $50M minimum 24h volume
    "min_volatility_pct": 1.5,        # 1.5% minimum daily volatility
    "max_spread_pct": 0.05,           # 0.05% max spread
    "sl_atr_multiplier": 1.5,         # SL = entry +/- 1.5 * ATR
    "tp_atr_multiplier": 3.0,         # TP = entry +/- 3.0 * ATR
    "trailing_stop_pct": 0.02,        # 2% trailing stop
    "max_hold_hours": 72,             # Max 72h position hold
}

# -- Leverage Tiers (volatility-based) -----------------------------
LEVERAGE_TIERS = [
    {"max_volatility": 0.02, "max_leverage": 8},
    {"max_volatility": 0.04, "max_leverage": 5},
    {"max_volatility": 0.06, "max_leverage": 3},
    {"max_volatility": float("inf"), "max_leverage": 2},
]

# -- Scanner -------------------------------------------------------
SCANNER = {
    "max_candidates": 10,
    "scan_interval_minutes": 30,
    "analysis_cooldown_hours": 4,
}

# -- Schedule ------------------------------------------------------
SCHEDULE = {
    "scan_interval_minutes": 30,
    "monitor_interval_minutes": 5,
    "status_interval_minutes": 10,
    "daily_report_hour": 23,
    "daily_report_minute": 0,
}

# -- WebSocket -----------------------------------------------------
WS = {
    "reconnect_delay": 5,
    "max_reconnect_attempts": 10,
    "heartbeat_interval": 30,
}

# -- Notifications -------------------------------------------------
DISCORD_WEBHOOK_ALERTS = os.getenv("DISCORD_WEBHOOK_ALERTS", "")
DISCORD_WEBHOOK_REPORTS = os.getenv("DISCORD_WEBHOOK_REPORTS", "")
