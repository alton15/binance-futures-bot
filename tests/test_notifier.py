"""Tests for notifier formatting helpers."""

from src.notifications.notifier import _fmt_price, _fmt_pnl


def test_fmt_price_low_price():
    """Prices below $100 should have 4 decimal places."""
    assert _fmt_price(0.5432) == "0.5432"
    assert _fmt_price(1.2345) == "1.2345"
    assert _fmt_price(99.9999) == "99.9999"


def test_fmt_price_high_price():
    """Prices at or above $100 should have 2 decimal places."""
    assert _fmt_price(100.0) == "100.00"
    assert _fmt_price(50000.1234) == "50000.12"
    assert _fmt_price(1234.5678) == "1234.57"


def test_fmt_price_zero():
    """Zero price should use 4 decimals."""
    assert _fmt_price(0.0) == "0.0000"


def test_fmt_price_boundary():
    """Boundary at exactly $100."""
    assert _fmt_price(99.99) == "99.9900"
    assert _fmt_price(100.00) == "100.00"


def test_fmt_pnl_positive():
    assert _fmt_pnl(1.5) == "+$1.5000"


def test_fmt_pnl_negative():
    assert _fmt_pnl(-2.3) == "$-2.3000"


def test_fmt_pnl_zero():
    assert _fmt_pnl(0.0) == "+$0.0000"
