"""Central, immutable configuration for the fade scalper.

All tunables live here. Values chosen by the backtest optimizer are recorded
in StrategyConfig defaults; override via environment only for ops settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"

# Binance USDT-M VIP0 fees (fraction of notional)
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005


@dataclass(frozen=True)
class StrategyConfig:
    """Volatility-breakout signal parameters (see backtest/optimize.py).

    Defaults = best walk-forward candidate (follow mode, 30m breakout, 15m hold).
    NOTE: 60-day validation found NO statistically robust edge for either
    follow or fade; run in paper mode only until forward results confirm.
    """

    follow: bool = True              # True: trade with the move; False: fade it
    lookback_s: int = 1800           # window to measure the move
    sigma_window_s: int = 7200       # window for 1s-return volatility
    z_entry: float = 4.0             # |move| / (sigma*sqrt(L)) threshold
    min_move_bps: float = 80.0       # ignore small absolute moves (fees dominate)
    burst_min: float = 0.0           # volume burst multiple, 0 = disabled
    tp_frac: float = 1.0             # take profit distance (x move)
    sl_frac: float = 0.5             # stop distance (x move)
    max_hold_s: int = 900            # time stop
    cooldown_s: int = 300            # per-symbol pause after exit
    maker_entry: bool = False        # follow mode needs taker entry
    maker_wait_s: int = 5            # cancel unfilled maker entry after this


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01     # fraction of equity lost if SL hits
    max_leverage: int = 10
    max_positions: int = 3
    max_notional_frac: float = 3.0   # total notional <= equity * this
    daily_loss_limit: float = 0.05   # halt for the UTC day
    max_drawdown: float = 0.30       # halt until manual reset
    min_notional_usdt: float = 5.0   # Binance minimum order notional
    liq_buffer: float = 2.0          # liquidation distance must be >= SL dist * this
    max_spread_bps: float = 5.0      # skip entries when the book is wide
    max_quote_age_s: float = 10.0    # older book quotes are not trusted for pricing


@dataclass(frozen=True)
class UniverseConfig:
    min_quote_volume_24h: float = 50_000_000.0
    max_symbols: int = 40
    refresh_s: int = 1800
    exclude: tuple[str, ...] = ("BTCDOMUSDT", "USDCUSDT")


@dataclass(frozen=True)
class OpsConfig:
    stale_stream_s: float = 15.0     # no message for this long => reconnect
    max_conn_age_s: int = 23 * 3600  # Binance drops connections at 24h
    status_interval_s: int = 1800
    paper_latency_s: float = 0.25    # simulated order latency in paper mode


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    mode: str                        # "paper" | "live"
    testnet: bool
    initial_capital: float
    discord_alerts: str = field(repr=False)
    discord_reports: str = field(repr=False)
    db_path: Path
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    ops: OpsConfig = field(default_factory=OpsConfig)

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


def load_settings(mode_override: str | None = None) -> Settings:
    """Load settings from the environment and validate them."""
    load_dotenv(BASE_DIR / ".env")
    mode = (mode_override or os.getenv("TRADING_MODE", "paper")).lower()
    if mode not in ("paper", "live"):
        raise ConfigError(f"TRADING_MODE must be paper or live, got {mode!r}")
    try:
        capital = float(os.getenv("INITIAL_CAPITAL", "700"))
    except ValueError as e:
        raise ConfigError("INITIAL_CAPITAL must be a number") from e
    if capital <= 0:
        raise ConfigError("INITIAL_CAPITAL must be positive")
    key = os.getenv("BINANCE_API_KEY", "")
    secret = os.getenv("BINANCE_API_SECRET", "")
    if mode == "live" and not (key and secret):
        raise ConfigError("Live mode requires BINANCE_API_KEY and BINANCE_API_SECRET")
    return Settings(
        api_key=key,
        api_secret=secret,
        mode=mode,
        testnet=os.getenv("BINANCE_TESTNET", "false").lower() == "true",
        initial_capital=capital,
        discord_alerts=os.getenv("DISCORD_WEBHOOK_ALERTS", ""),
        discord_reports=os.getenv("DISCORD_WEBHOOK_REPORTS", ""),
        db_path=Path(os.getenv("SCALPER_DB_PATH", str(DATA_DIR / "scalper.db"))),
    )
