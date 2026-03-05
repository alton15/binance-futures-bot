"""Trading profile presets - conservative, neutral, aggressive."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config.settings import RISK, SIGNALS, LEVERAGE_TIERS

# Default values for profile-only risk keys (not in global RISK)
_PROFILE_RISK_DEFAULTS: dict[str, Any] = {
    "max_margin_per_trade_pct": 0.15,
}


@dataclass(frozen=True)
class ProfileConfig:
    """Immutable trading profile configuration.

    Each profile overrides specific risk/signal/leverage parameters.
    Unset keys fall back to global settings.
    """

    name: str
    label: str
    risk: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    leverage_tiers: list[dict[str, Any]] = field(default_factory=list)
    leverage_min: int = 2
    leverage_max: int = 8

    def get_risk(self, key: str) -> Any:
        """Get a risk parameter, falling back to global RISK config."""
        if key in self.risk:
            return self.risk[key]
        if key in RISK:
            return RISK[key]
        return _PROFILE_RISK_DEFAULTS[key]

    def get_signal(self, key: str) -> Any:
        """Get a signal parameter, falling back to global SIGNALS config."""
        return self.signals.get(key, SIGNALS[key])

    def get_leverage_tiers(self) -> list[dict[str, Any]]:
        """Get leverage tiers, falling back to global LEVERAGE_TIERS."""
        return self.leverage_tiers if self.leverage_tiers else list(LEVERAGE_TIERS)


CONSERVATIVE = ProfileConfig(
    name="conservative",
    label="Conservative",
    risk={
        "risk_per_trade_pct": 0.015,
        "max_open_positions": 3,
        "max_exposure_pct": 0.40,
        "daily_loss_limit_pct": 0.04,
        "max_drawdown_pct": 0.10,
        "signal_strength_min": 0.70,
        "sl_atr_multiplier": 2.0,
        "tp_atr_multiplier": 3.0,
        "trailing_stop_pct": 0.015,
        "max_hold_hours": 48,
        "liquidation_buffer_pct": 0.30,
        "max_margin_per_trade_pct": 0.12,
    },
    signals={
        "min_confirming": 5,
        "min_strength": 0.65,
    },
    leverage_tiers=[
        {"max_volatility": 0.02, "max_leverage": 3},
        {"max_volatility": 0.04, "max_leverage": 2},
        {"max_volatility": float("inf"), "max_leverage": 1},
    ],
    leverage_min=1,
    leverage_max=3,
)

NEUTRAL = ProfileConfig(
    name="neutral",
    label="Neutral",
    risk={
        "risk_per_trade_pct": 0.02,
        "max_open_positions": 5,
        "max_exposure_pct": 0.60,
        "daily_loss_limit_pct": 0.06,
        "max_drawdown_pct": 0.20,
        "signal_strength_min": 0.65,
        "sl_atr_multiplier": 1.5,
        "tp_atr_multiplier": 3.0,
        "trailing_stop_pct": 0.02,
        "max_hold_hours": 72,
        "liquidation_buffer_pct": 0.20,
        "max_margin_per_trade_pct": 0.15,
    },
    signals={
        "min_confirming": 4,
        "min_strength": 0.60,
    },
    leverage_tiers=[
        {"max_volatility": 0.02, "max_leverage": 6},
        {"max_volatility": 0.04, "max_leverage": 4},
        {"max_volatility": 0.06, "max_leverage": 3},
        {"max_volatility": float("inf"), "max_leverage": 2},
    ],
    leverage_min=2,
    leverage_max=6,
)

AGGRESSIVE = ProfileConfig(
    name="aggressive",
    label="Aggressive",
    risk={
        "risk_per_trade_pct": 0.03,
        "max_open_positions": 5,
        "max_exposure_pct": 0.70,
        "daily_loss_limit_pct": 0.08,
        "max_drawdown_pct": 0.25,
        "signal_strength_min": 0.60,
        "sl_atr_multiplier": 1.5,
        "tp_atr_multiplier": 3.0,
        "trailing_stop_pct": 0.025,
        "max_hold_hours": 72,
        "liquidation_buffer_pct": 0.15,
        "max_margin_per_trade_pct": 0.15,
    },
    signals={
        "min_confirming": 4,
        "min_strength": 0.55,
    },
    leverage_tiers=[
        {"max_volatility": 0.02, "max_leverage": 10},
        {"max_volatility": 0.04, "max_leverage": 7},
        {"max_volatility": 0.06, "max_leverage": 5},
        {"max_volatility": float("inf"), "max_leverage": 3},
    ],
    leverage_min=3,
    leverage_max=10,
)

SCALP = ProfileConfig(
    name="scalp",
    label="Scalp",
    risk={
        "risk_per_trade_pct": 0.01,          # 1% (높은 레버리지 보상)
        "max_open_positions": 8,
        "max_exposure_pct": 0.60,
        "daily_loss_limit_pct": 0.05,
        "max_drawdown_pct": 0.15,
        "signal_strength_min": 0.60,
        "sl_atr_multiplier": 1.5,            # 1.5x ATR (노이즈 필터링)
        "tp_atr_multiplier": 3.0,            # 3.0x ATR (R:R 1:2 유지)
        "trailing_stop_pct": 0.01,
        "max_hold_hours": 4,
        "liquidation_buffer_pct": 0.15,      # 15% (기존 20%)
        "max_margin_per_trade_pct": 0.10,
        "analysis_cooldown_seconds": 60,
    },
    signals={
        "min_confirming": 4,
        "min_strength": 0.60,
    },
    leverage_tiers=[
        {"max_volatility": 0.02, "max_leverage": 15},
        {"max_volatility": 0.04, "max_leverage": 12},
        {"max_volatility": 0.06, "max_leverage": 8},
        {"max_volatility": float("inf"), "max_leverage": 5},
    ],
    leverage_min=5,
    leverage_max=15,
)

# Swing profiles for scheduled pipeline (scan → analyze → trade)
SWING_PROFILES: list[ProfileConfig] = [CONSERVATIVE, NEUTRAL, AGGRESSIVE]

# Scalp profiles for WebSocket event-driven pipeline
SCALP_PROFILES: list[ProfileConfig] = [SCALP]

# All profiles (swing only - scalp runs in separate process)
ALL_PROFILES: list[ProfileConfig] = SWING_PROFILES

_PROFILE_MAP: dict[str, ProfileConfig] = {
    p.name: p for p in SWING_PROFILES + SCALP_PROFILES
}


def get_profile(name: str) -> ProfileConfig:
    """Get a profile by name. Raises ValueError for unknown names."""
    profile = _PROFILE_MAP.get(name)
    if profile is None:
        valid = ", ".join(_PROFILE_MAP.keys())
        raise ValueError(f"Unknown profile '{name}'. Valid profiles: {valid}")
    return profile
