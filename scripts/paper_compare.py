"""Side-by-side paper results of every bot version (read-only).

Safe while the bots are running: each database is read from a private copy (db + -wal), never in place.
Opening a live WAL database from the host shares the bot's -shm index over the Docker Desktop bind mount,
where file locks do not work, so the reader can see stale rows or rewrite the writer's index.

Usage:
    python -m scripts.paper_compare [--data data] [--capital 700]      # table on stdout
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("paper_compare")            # diagnostics -> stderr
report = logging.getLogger("paper_compare.report")     # the table -> stdout

LEGACY_DB = "futures_bot.db"
SCALPER_DB = "scalper.db"
TREND_DBS = {"trend_lo": "trend_trend_lo.db", "trend_ls": "trend_trend_ls.db"}
SNAPSHOT_SUFFIXES = ("", "-wal")                       # never the -shm: the copy rebuilds its own index
SNAPSHOT_RETRIES = 3                                   # a copy taken mid-checkpoint can be torn


@dataclass(frozen=True)
class Row:
    name: str
    equity: float
    trades: int
    wins: int
    open_pos: int
    note: str = ""


def _copy_db(path: Path, dest: Path) -> None:
    for suffix in SNAPSHOT_SUFFIXES:
        src = Path(f"{path}{suffix}")
        if src.exists():
            shutil.copyfile(src, f"{dest}{suffix}")


def _is_consistent(path: Path) -> bool:
    try:
        with closing(sqlite3.connect(path)) as db:
            return db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False


@contextmanager
def snapshot(path: Path) -> Iterator[sqlite3.Connection]:
    """Connection to a consistent private copy of `path`; the live files are only ever read."""
    with tempfile.TemporaryDirectory(prefix="paper_compare_") as tmp:
        for attempt in range(1, SNAPSHOT_RETRIES + 1):
            copy = Path(tmp) / f"{attempt}_{path.name}"
            try:
                _copy_db(path, copy)
            except OSError as e:                       # -wal removed by the writer between exists/copy
                logger.warning("copy of %s failed (%s), retrying", path, e)
                continue
            if _is_consistent(copy):
                with closing(sqlite3.connect(copy)) as db:
                    yield db
                return
            if attempt < SNAPSHOT_RETRIES:
                logger.warning("snapshot of %s is torn, retrying", path)
        raise sqlite3.DatabaseError(f"no consistent snapshot of {path} after {SNAPSHOT_RETRIES} tries")


def legacy_rows(path: Path, capital: float) -> list[Row]:
    """One row per legacy profile: capital + realized (closed) + unrealized (open) PnL."""
    with snapshot(path) as db:
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
    with snapshot(path) as db:
        n, w, pnl = db.execute("SELECT COUNT(*), SUM(net_pnl > 0), COALESCE(SUM(net_pnl), 0) FROM positions "
                               "WHERE mode='paper' AND status='closed'").fetchone()
        (o,) = db.execute("SELECT COUNT(*) FROM positions WHERE mode='paper' AND status='open'").fetchone()
    return Row("scalp", capital + pnl, int(n), int(w or 0), int(o), "realized only")


def trend_row(name: str, path: Path, capital: float) -> Row:
    with snapshot(path) as db:
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


def _print_table(rows: list[Row], capital: float) -> None:
    report.info("%-18s %10s %8s %7s %6s  %s", "version", "equity", "return", "trades", "open", "note")
    for r in rows:
        win = f" win {r.wins / r.trades:.0%}" if r.trades and r.wins else ""
        report.info("%-18s %10.2f %+7.2f%% %7d %6d  %s%s", r.name, r.equity,
                    (r.equity / capital - 1) * 100, r.trades, r.open_pos, r.note, win)
    report.info("trades = closed positions (bot/scalp) or fills (trend); bot/trend equity includes open PnL")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--capital", type=float, default=700.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(logging.Formatter("%(message)s"))
    report.addHandler(stdout)
    report.propagate = False
    report.setLevel(logging.INFO)
    try:
        _print_table(collect(args.data, args.capital), args.capital)
    finally:
        report.removeHandler(stdout)


if __name__ == "__main__":
    main()
