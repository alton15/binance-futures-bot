import pytest

from src.scalper.config import TAKER_FEE, RiskConfig
from src.scalper.risk.guard import RiskGuard
from src.scalper.risk.sizing import STOP_SLIPPAGE, size_position

DAY = 86400.0
T0 = 1_800_000_000.0


class TestSizing:
    def test_loss_at_stop_equals_risk_budget(self):
        cfg = RiskConfig(risk_per_trade=0.01, max_leverage=20, max_positions=1)
        r = size_position(1000, 100.0, 99.0, True, cfg, qty_step=0.0001, min_notional=5)
        assert r.ok
        loss = r.qty * (1.0 + 100 * 0.0002 + 99 * (TAKER_FEE + STOP_SLIPPAGE))
        assert loss == pytest.approx(10.0, rel=1e-3)
        assert r.risk_usdt <= 10.0 + 1e-9

    def test_notional_capped_by_leverage_slice(self):
        cfg = RiskConfig(risk_per_trade=0.05, max_leverage=5, max_positions=3)
        r = size_position(900, 100.0, 99.9, False, cfg, qty_step=0.001, min_notional=5)
        assert r.notional <= 900 / 3 * 5 + 1e-6
        assert r.margin <= 300 + 1e-6

    def test_liquidation_buffer_limits_leverage(self):
        cfg = RiskConfig(risk_per_trade=0.05, max_leverage=50, max_positions=1, liq_buffer=2.0)
        r = size_position(1000, 100.0, 95.0, False, cfg, qty_step=0.001, min_notional=5)
        # 5% stop, 2x buffer => liquidation >= 10% away => leverage <= ~9
        assert r.leverage <= 9

    def test_rejects_below_min_notional(self):
        cfg = RiskConfig(risk_per_trade=0.0001)
        r = size_position(100, 100.0, 90.0, False, cfg, qty_step=0.001, min_notional=5)
        assert not r.ok and "minimum" in r.reason

    def test_qty_respects_step(self):
        r = size_position(1000, 3.3333, 3.30, True, RiskConfig(), qty_step=1.0, min_notional=5)
        assert r.qty == int(r.qty)

    @pytest.mark.parametrize("entry,sl", [(0, 1), (100, 100), (100, 0)])
    def test_invalid_inputs_rejected(self, entry, sl):
        assert not size_position(1000, entry, sl, True, RiskConfig(), 0.001, 5).ok


class TestGuard:
    def test_blocks_busy_symbol_and_max_positions(self):
        g = RiskGuard(RiskConfig(max_positions=2), 1000, now=T0)
        g.reserve("A")
        assert not g.can_enter("A", T0).allowed
        g.reserve("B")
        assert g.can_enter("C", T0).reason == "max positions"

    def test_cooldown_after_release(self):
        g = RiskGuard(RiskConfig(), 1000, now=T0)
        g.reserve("A")
        g.release("A", T0, cooldown_s=60)
        assert g.can_enter("A", T0 + 30).reason == "cooldown"
        assert g.can_enter("A", T0 + 61).allowed

    def test_daily_loss_halts_and_resets_next_day(self):
        g = RiskGuard(RiskConfig(daily_loss_limit=0.05, max_drawdown=0.5), 1000, now=T0)
        g.record_pnl(-60, T0 + 10)
        assert not g.can_enter("A", T0 + 20).allowed
        assert g.can_enter("A", T0 + DAY).allowed

    def test_max_drawdown_requires_manual_reset(self):
        g = RiskGuard(RiskConfig(daily_loss_limit=0.9, max_drawdown=0.2), 1000, now=T0)
        g.record_pnl(-250, T0)
        assert not g.can_enter("A", T0 + 2 * DAY).allowed
        assert "drawdown" in g.halted_reason

    def test_equity_and_peak_tracking(self):
        g = RiskGuard(RiskConfig(), 1000, now=T0)
        g.record_pnl(50, T0)
        g.record_pnl(-20, T0)
        assert g.equity == pytest.approx(1030)
        assert g.peak == pytest.approx(1050)


def test_guard_restart_keeps_daily_and_drawdown_halts():
    from src.scalper.config import RiskConfig
    from src.scalper.risk.guard import RiskGuard
    cfg = RiskConfig(daily_loss_limit=0.05, max_drawdown=0.30)
    day = RiskGuard(cfg, equity=940.0, peak_equity=1000.0, now=0.0, day_start_equity=1000.0)
    assert day.halted_reason.startswith("daily loss")
    dd = RiskGuard(cfg, equity=690.0, peak_equity=1000.0, now=0.0, day_start_equity=690.0)
    assert dd.halted_reason.startswith("max drawdown")
    assert not dd.can_enter("X", 1.0).allowed
