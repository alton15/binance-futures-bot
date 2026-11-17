"""Named trend profiles selected from the in-sample grid (2021-01 .. 2025-10) only.

Selection rule, fixed before looking at the holdout:
  * trend_lo: highest IS Sharpe whose neighbours (u10/u20, ema/tsmom) were also top-ranked
  * trend_ls: highest IS worst-calendar-year Sharpe (the only variant positive in every IS year)
Holdout (2025-11 .. 2026-10) results are reported in the README, not used for choice.
"""

from __future__ import annotations

from dataclasses import replace

from src.trend.config import BARS_PER_DAY, PortfolioConfig, SignalConfig, TrendProfile

PROFILES: dict[str, TrendProfile] = {
    "trend_lo": TrendProfile(
        name="trend_lo",
        signal=SignalConfig(kind="ema", lookbacks_d=(5, 10, 20), long_only=True, regime_filter=True),
        portfolio=replace(PortfolioConfig(), universe_size=20, rebalance_every=BARS_PER_DAY),
    ),
    "trend_ls": TrendProfile(
        name="trend_ls",
        signal=SignalConfig(kind="tsmom", lookbacks_d=(10, 20, 40), long_only=False, regime_filter=False),
        portfolio=replace(PortfolioConfig(), universe_size=20, rebalance_every=1),
    ),
}


def get_profile(name: str) -> TrendProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown trend profile {name!r}; choose from {sorted(PROFILES)}") from None
