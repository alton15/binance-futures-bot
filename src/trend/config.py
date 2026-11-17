"""Central, immutable configuration for the multi-asset trend follower.

Backtest and paper engine share these dataclasses, so a profile validated in
the backtest is exactly what the engine trades.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
TREND_DIR = DATA_DIR / "trend"
RAW_DIR = TREND_DIR / "raw"
PANEL_PATH = TREND_DIR / "panel_4h.npz"

BAR_S = 4 * 3600
REGIME_SYMBOL = "BTCUSDT"            # market proxy for the regime filter
BARS_PER_DAY = 6
DAYS_PER_YEAR = 365

# Binance USDT-M VIP0 (fraction of notional). Trend entries/exits are taker.
TAKER_FEE = 0.0005
SLIPPAGE = 0.0003                    # per side; conservative for top-20 perps
# Baseline funding (0.01% / 8h) used where the exchange history is missing.
BASELINE_FUNDING_PER_BAR = 0.0001 * BAR_S / (8 * 3600)


@dataclass(frozen=True)
class SignalConfig:
    """Trend signal = mean over lookbacks of a per-lookback {-1, 0, +1} vote."""

    kind: str = "donchian"           # donchian | tsmom | ema
    lookbacks_d: tuple[int, ...] = (20, 40, 80)
    long_only: bool = False
    regime_filter: bool = False      # longs only while BTC's own signal > 0, shorts only while < 0


@dataclass(frozen=True)
class PortfolioConfig:
    universe_size: int = 20          # top-N by trailing quote volume (point in time)
    volume_window_d: int = 30
    min_history_d: int = 90          # skip freshly listed contracts
    vol_window_d: int = 30           # realized vol for inverse-vol sizing
    target_vol: float = 0.50         # annualized vol budget per asset slot
    max_weight: float = 0.25         # |weight| cap per asset (x equity)
    max_gross: float = 2.0           # sum |weight| cap (x equity)
    rebalance_every: int = BARS_PER_DAY   # bars between rebalance checks (6 = daily)
    rebalance_band: float = 0.25     # trade only if |dw| > band * |target| (or sign flips)


@dataclass(frozen=True)
class CostConfig:
    fee: float = TAKER_FEE
    slippage: float = SLIPPAGE

    @property
    def per_side(self) -> float:
        return self.fee + self.slippage


@dataclass(frozen=True)
class TrendProfile:
    name: str
    signal: SignalConfig = field(default_factory=SignalConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    costs: CostConfig = field(default_factory=CostConfig)


@dataclass(frozen=True)
class TrendRiskConfig:
    max_drawdown: float = 0.35       # halt (flatten) until manual reset
    min_notional_usdt: float = 5.0


@dataclass(frozen=True)
class TrendSettings:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    mode: str
    initial_capital: float
    discord_alerts: str = field(repr=False)
    discord_reports: str = field(repr=False)
    db_path: Path
    profile: TrendProfile
    risk: TrendRiskConfig = field(default_factory=TrendRiskConfig)
    testnet: bool = False            # read by the shared Exchange wrapper

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


def load_settings(profile: TrendProfile, mode_override: str | None = None) -> TrendSettings:
    load_dotenv(BASE_DIR / ".env")
    mode = (mode_override or os.getenv("TRADING_MODE", "paper")).lower()
    if mode not in ("paper", "live"):
        raise ConfigError(f"TRADING_MODE must be paper or live, got {mode!r}")
    try:
        capital = float(os.getenv("TREND_INITIAL_CAPITAL", os.getenv("INITIAL_CAPITAL", "700")))
    except ValueError as e:
        raise ConfigError("INITIAL_CAPITAL must be a number") from e
    if capital <= 0:
        raise ConfigError("INITIAL_CAPITAL must be positive")
    default_db = DATA_DIR / f"trend_{profile.name}.db"
    return TrendSettings(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        mode=mode,
        initial_capital=capital,
        discord_alerts=os.getenv("DISCORD_WEBHOOK_ALERTS", ""),
        discord_reports=os.getenv("DISCORD_WEBHOOK_REPORTS", ""),
        db_path=Path(os.getenv("TREND_DB_PATH", str(default_db))),
        profile=profile,
        testnet=os.getenv("BINANCE_TESTNET", "false").lower() == "true",
    )
