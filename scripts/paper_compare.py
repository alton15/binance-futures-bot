"""Side-by-side paper results of every bot version (read-only).

Usage:
    python -m scripts.paper_compare [--data data] [--capital 700]
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("paper_compare")

LEGACY_DB = "futures_bot.db"
SCALPER_DB = "scalper.db"
TREND_DBS = {"trend_lo": "trend_trend_lo.db", "trend_ls": "trend_trend_ls.db"}


@dataclass(frozen=True)
class Row:
    name: str
    equity: float
    trades: int
    wins: int
    open_pos: int
    note: str = ""


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def legacy_rows(path: Path, capital: float) -> list[Row]:
    """One row per legacy profile: capital + realized (closed) + unrealized (open) PnL."""
    with closing(_connect(path)) as db:
        rows = db.execute(
            "SELECT profile, "
            "SUM(CASE WHEN status='closed' THEN realized_pnl ELSE 0 END), "
            "SUM(CASE WHEN status='open' THEN COALESCE(unrealized_pnl, 0) ELSE 0 END), "
            "SUM(status='closed'), SUM(status='closed' AND realized_pnl > 0), SUM(status='open') "
            "FROM positions WHERE is_paper=1 GROUP BY profile").fetchall()
    if not rows:
        return [Row("bot", capital, 0, 0, 0, "no positions yet")]
    return [Row(f"bot:{p}", capital + (r or 0) + (u or 0), int(n or 0), int(w or 0), int(o or 0))
            for p, r, u, n, w, o in rows]


def scalper_row(path: Path, capital: float) -> Row:
    with closing(_connect(path)) as db:
        n, w, pnl = db.execute("SELECT COUNT(*), SUM(net_pnl > 0), COALESCE(SUM(net_pnl), 0) FROM positions "
                               "WHERE mode='paper' AND status='closed'").fetchone()
        (o,) = db.execute("SELECT COUNT(*) FROM positions WHERE mode='paper' AND status='open'").fetchone()
    return Row("scalp", capital + pnl, int(n), int(w or 0), int(o), "realized only")


def trend_row(name: str, path: Path, capital: float) -> Row:
    with closing(_connect(path)) as db:
        last = db.execute("SELECT equity, n_pos FROM equity ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
        n_fills, fees = db.execute("SELECT COUNT(*), COALESCE(SUM(fee), 0) FROM fills").fetchone()
        (funding,) = db.execute("SELECT COALESCE(SUM(amount), 0) FROM funding").fetchone()
        halted = db.execute("SELECT value FROM meta WHERE key='halted'").fetchone()
    equity, n_pos = last if last else (capital, 0)
    note = f"fees ${fees:.2f} funding ${funding:+.2f}" + (" HALTED" if halted and halted[0] == "1" else "")
    return Row(name, equity, int(n_fills), 0, int(n_pos), note)


def collect(data: Path, capital: float) -> list[Row]:
    out: list[Row] = []
    readers = [(data / LEGACY_DB, lambda p: legacy_rows(p, capital)),
               (data / SCALPER_DB, lambda p: [scalper_row(p, capital)])]
    readers += [(data / f, lambda p, n=n: [trend_row(n, p, capital)]) for n, f in TREND_DBS.items()]
    for path, read in readers:
        if not path.exists():
            logger.warning("missing %s", path)
            continue
        try:
            out.extend(read(path))
        except sqlite3.Error as e:
            logger.error("cannot read %s: %s", path, e)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--capital", type=float, default=700.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("%-18s %10s %8s %7s %6s  %s", "version", "equity", "return", "trades", "open", "note")
    for r in collect(args.data, args.capital):
        win = f" win {r.wins / r.trades:.0%}" if r.trades and r.wins else ""
        logger.info("%-18s %10.2f %+7.2f%% %7d %6d  %s%s", r.name, r.equity,
                    (r.equity / args.capital - 1) * 100, r.trades, r.open_pos, r.note, win)
    logger.info("trades = closed positions (bot/scalp) or fills (trend); bot/trend equity includes open PnL")


if __name__ == "__main__":
    main()
