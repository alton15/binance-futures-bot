"""Trading profile presets - conservative, neutral, aggressive."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config.settings import RISK, SIGNALS, LEVERAGE_TIERS

# Default values for profile-only risk keys (not in global RISK)
_PROFILE_RISK_DEFAULTS: dict[str, Any] = {
    "max_margin_per_trade_pct": 0.15,
    "analysis_cooldown_seconds": 60,
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
        "signal_strength_min": 0.65,             # 0.70 → 0.65: 거래 빈도 증가 (표본 부족 해소)
        "sl_atr_multiplier": 2.0,
        "tp_atr_multiplier": 4.0,
        "trailing_stop_pct": 0.03,               # 3% (크립토 변동성 대비)
        "trailing_activation_atr": 1.5,           # 1.0 → 1.5: 충분한 수익 후 트레일링 활성화
        "trailing_atr_multiplier": 1.5,           # ATR 기반 동적 트레일링 거리
        "max_hold_hours": 48,
        "liquidation_buffer_pct": 0.30,
        "max_margin_per_trade_pct": 0.12,
    },
    signals={
        "min_confirming": 4,                      # 5 → 4: 거래 빈도 증가
        "min_strength": 0.60,                     # 0.65 → 0.60: 약간 완화
        "macd_opposition_penalty": 1.0,           # MACD 반대 시 거부
        "low_volume_threshold": 0.5,
        "low_volume_penalty": 1.0,
        "bb_conflict_penalty": 0.20,
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
        "sl_atr_multiplier": 2.0,
        "tp_atr_multiplier": 4.0,
        "trailing_stop_pct": 0.03,               # 3%
        "trailing_activation_atr": 1.2,           # 1.0 → 1.2: 트레일링 손실 방지
        "trailing_atr_multiplier": 1.2,           # 1.5 → 1.2: 활성화 후 더 타이트하게
        "max_hold_hours": 72,
        "liquidation_buffer_pct": 0.20,
        "max_margin_per_trade_pct": 0.15,
    },
    signals={
        "min_confirming": 4,
        "min_strength": 0.55,                    # 0.60 → 0.55: 약간 완화 (거래 빈도↑)
        "macd_opposition_penalty": 0.30,
        "low_volume_threshold": 0.5,
        "low_volume_penalty": 0.15,
        "bb_conflict_penalty": 0.10,
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
        "risk_per_trade_pct": 0.02,              # 0.03 → 0.02: 손실 규모 축소 (26.7% WR)
        "max_open_positions": 5,
        "max_exposure_pct": 0.70,
        "daily_loss_limit_pct": 0.08,
        "max_drawdown_pct": 0.25,
        "signal_strength_min": 0.65,             # 0.60 → 0.65: 진입 품질 강화
        "sl_atr_multiplier": 2.0,
        "tp_atr_multiplier": 4.0,
        "trailing_stop_pct": 0.035,              # 3.5%
        "trailing_activation_atr": 1.2,           # 0.8 → 1.2: 조기 트레일링 손실 방지
        "trailing_atr_multiplier": 1.2,           # 타이트한 ATR 트레일링
        "max_hold_hours": 72,
        "liquidation_buffer_pct": 0.15,
        "max_margin_per_trade_pct": 0.15,
    },
    signals={
        "min_confirming": 5,                     # 4 → 5: 충분한 확인 필요 (WR 26.7%)
        "min_strength": 0.60,                    # 0.55 → 0.60: 강화
        "macd_opposition_penalty": 0.30,         # 0.15 → 0.30: MACD 반대 패널티 강화
        "low_volume_threshold": 0.0,
        "low_volume_penalty": 0.0,
        "bb_conflict_penalty": 0.0,
    },
    leverage_tiers=[
        {"max_volatility": 0.02, "max_leverage": 8},   # 10 → 8
        {"max_volatility": 0.04, "max_leverage": 6},    # 7 → 6
        {"max_volatility": 0.06, "max_leverage": 4},    # 5 → 4
        {"max_volatility": float("inf"), "max_leverage": 3},
    ],
    leverage_min=3,
    leverage_max=8,                              # 10 → 8: 손실 제한
)

SCALP = ProfileConfig(
    name="scalp",
    label="Scalp",
    risk={
        "risk_per_trade_pct": 0.008,         # 0.01 → 0.008: 이상치 손실 관리 (포지션 축소)
        "max_open_positions": 3,             # 5 → 3: 집중도 높이기
        "max_exposure_pct": 0.60,
        "daily_loss_limit_pct": 0.07,
        "max_drawdown_pct": 0.20,
        "signal_strength_min": 0.45,         # 0.40 → 0.45: 약한 시그널 필터링
        "sl_atr_multiplier": 3.0,            # 2.5 → 3.0: 노이즈 SL 트리거 감소
        "tp_atr_multiplier": 3.5,            # 4.0 → 3.5: TP 도달률 향상 (R:R 1:1.17)
        "trailing_stop_pct": 0.02,           # 0.025 → 0.02: 수익 확보 시 타이트하게 보호
        "trailing_activation_atr": 1.2,      # 0.8 → 1.2: 충분한 수익 후 트레일링 활성화
        "trailing_atr_multiplier": 0.8,      # 1.0 → 0.8: ATR 트레일링 더 타이트하게
        "max_hold_hours": 4,
        "liquidation_buffer_pct": 0.15,
        "max_margin_per_trade_pct": 0.10,
        "analysis_cooldown_seconds": 60,
    },
    signals={
        "min_confirming": 3,                 # 2 → 3: 진입 품질 강화 (false entry 줄이기)
        "min_strength": 0.45,                # 0.40 → 0.45: 약한 시그널 필터링
        "macd_opposition_penalty": 0.20,
        "low_volume_threshold": 0.0,
        "low_volume_penalty": 0.0,
        "bb_conflict_penalty": 0.0,
    },
    leverage_tiers=[
        {"max_volatility": 0.02, "max_leverage": 10},   # 15 → 10
        {"max_volatility": 0.04, "max_leverage": 8},     # 12 → 8
        {"max_volatility": 0.06, "max_leverage": 6},     # 8 → 6
        {"max_volatility": float("inf"), "max_leverage": 5},
    ],
    leverage_min=5,
    leverage_max=10,                         # 15 → 10: 5x>6x 데이터 기반
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
