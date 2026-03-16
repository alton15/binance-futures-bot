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
        "signal_strength_min": 0.70,
        "sl_atr_multiplier": 2.0,
        "tp_atr_multiplier": 4.0,
        "trailing_stop_pct": 0.03,              # 2% → 3% (크립토 변동성 대비)
        "trailing_activation_atr": 1.0,          # 1x ATR 수익 후 트레일링 활성화
        "trailing_atr_multiplier": 1.5,          # ATR 기반 동적 트레일링 거리
        "max_hold_hours": 48,
        "liquidation_buffer_pct": 0.30,
        "max_margin_per_trade_pct": 0.12,
    },
    signals={
        "min_confirming": 5,
        "min_strength": 0.65,
        "macd_opposition_penalty": 1.0,          # MACD 반대 시 거부 (강도 × 1.0 감쇄)
        "low_volume_threshold": 0.5,             # 0.5x avg 미만 거부
        "low_volume_penalty": 1.0,               # 저볼륨 시 거부
        "bb_conflict_penalty": 0.20,             # BB 충돌 시 강도 -20%
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
        "trailing_stop_pct": 0.03,              # 2% → 3%
        "trailing_activation_atr": 1.0,          # 1x ATR 수익 후 활성화
        "trailing_atr_multiplier": 1.5,          # ATR 기반 동적 트레일링
        "max_hold_hours": 72,
        "liquidation_buffer_pct": 0.20,
        "max_margin_per_trade_pct": 0.15,
    },
    signals={
        "min_confirming": 4,
        "min_strength": 0.60,
        "macd_opposition_penalty": 0.30,         # MACD 반대 시 강도 -30%
        "low_volume_threshold": 0.5,             # 0.5x avg 미만
        "low_volume_penalty": 0.15,              # 저볼륨 시 강도 -15%
        "bb_conflict_penalty": 0.10,             # BB 충돌 시 강도 -10%
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
        "sl_atr_multiplier": 2.0,
        "tp_atr_multiplier": 4.0,
        "trailing_stop_pct": 0.035,             # 2.5% → 3.5%
        "trailing_activation_atr": 0.8,          # 0.8x ATR (더 빨리 활성화)
        "trailing_atr_multiplier": 1.2,          # 타이트한 ATR 트레일링
        "max_hold_hours": 72,
        "liquidation_buffer_pct": 0.15,
        "max_margin_per_trade_pct": 0.15,
    },
    signals={
        "min_confirming": 4,
        "min_strength": 0.55,
        "macd_opposition_penalty": 0.15,         # MACD 반대 시 강도 -15% (느슨)
        "low_volume_threshold": 0.0,             # 볼륨 필터 없음
        "low_volume_penalty": 0.0,
        "bb_conflict_penalty": 0.0,              # BB 필터 없음
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
        "max_open_positions": 5,             # 3 → 5: 스캘핑 기회 확대
        "max_exposure_pct": 0.60,
        "daily_loss_limit_pct": 0.07,        # 5% → 7%: 고빈도 스캘핑 여유
        "max_drawdown_pct": 0.20,            # 15% → 20%: 누적 드로다운 여유
        "signal_strength_min": 0.40,         # 0.50 → 0.40: MTF 감쇄 후에도 통과
        "sl_atr_multiplier": 2.5,            # 2.5x ATR (노이즈 필터링 강화)
        "tp_atr_multiplier": 4.0,            # 4.0x ATR (R:R 1:1.6 유지)
        "trailing_stop_pct": 0.025,          # 1.5% → 2.5% (3분봉 노이즈 대비)
        "trailing_activation_atr": 0.8,      # 0.8x ATR (스캘프는 빠른 활성화)
        "trailing_atr_multiplier": 1.0,      # 1x ATR 트레일링 거리
        "max_hold_hours": 4,
        "liquidation_buffer_pct": 0.15,      # 20% → 15%: aggressive와 동일
        "max_margin_per_trade_pct": 0.10,
        "analysis_cooldown_seconds": 60,
    },
    signals={
        "min_confirming": 2,                 # 3 → 2: 3분봉 노이즈 감안, 지표 2개 동의면 충분
        "min_strength": 0.40,                # 0.50 → 0.40: MTF 0일 때 50% 감쇄 후에도 통과
        "macd_opposition_penalty": 0.20,     # MACD 반대 시 강도 -20%
        "low_volume_threshold": 0.0,         # 볼륨 필터 제거 (스파이크 감지가 이미 볼륨 체크)
        "low_volume_penalty": 0.0,           # aggressive와 동일: 볼륨 패널티 없음
        "bb_conflict_penalty": 0.0,          # aggressive와 동일: BB 패널티 없음
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
