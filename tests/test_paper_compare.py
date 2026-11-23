"""paper_compare: reads a private snapshot of live WAL databases and prints the table on stdout."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from scripts import paper_compare as pc


def _wal_db(path: Path, ddl: str) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")             # keep every commit in the -wal file
    db.executescript(ddl)
    db.commit()
    return db


def _scalper(path: Path, pnls: list[float]) -> sqlite3.Connection:
    db = _wal_db(path, "CREATE TABLE positions (mode TEXT, status TEXT, net_pnl REAL);")
    db.executemany("INSERT INTO positions VALUES ('paper', 'closed', ?)", [(p,) for p in pnls])
    db.execute("INSERT INTO positions VALUES ('paper', 'open', NULL)")
    db.commit()
    return db


def test_snapshot_sees_uncheckpointed_rows_and_leaves_live_files_alone(tmp_path):
    live = tmp_path / pc.SCALPER_DB
    writer = _scalper(live, [3.0, -1.0])                 # writer stays open: rows live only in -wal
    try:
        assert Path(f"{live}-wal").stat().st_size > 0
        before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
        row = pc.scalper_row(live, 700.0)
        assert (row.equity, row.trades, row.wins, row.open_pos) == (702.0, 2, 1, 1)
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    finally:
        writer.close()


def test_snapshot_retries_then_fails_on_a_corrupt_copy(tmp_path, caplog):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite database" * 100)
    with pytest.raises(sqlite3.DatabaseError, match="consistent snapshot"):
        with pc.snapshot(bad):
            pass
    assert caplog.text.count("retrying") == pc.SNAPSHOT_RETRIES - 1


def test_collect_reads_every_version(tmp_path):
    legacy = _wal_db(tmp_path / pc.LEGACY_DB, "CREATE TABLE positions (profile TEXT, status TEXT, "
                     "realized_pnl REAL, unrealized_pnl REAL, is_paper INTEGER);")
    legacy.executemany("INSERT INTO positions VALUES (?,?,?,?,1)",
                       [("conservative", "closed", 5.0, None), ("conservative", "open", None, -2.0)])
    legacy.commit()
    scalp = _scalper(tmp_path / pc.SCALPER_DB, [-4.0])
    trend = _wal_db(tmp_path / pc.TREND_DBS["trend_ls"], "CREATE TABLE equity (ts REAL, equity REAL, n_pos INT);"
                    "CREATE TABLE fills (fee REAL); CREATE TABLE funding (amount REAL);"
                    "CREATE TABLE meta (key TEXT, value TEXT);")
    trend.executemany("INSERT INTO equity VALUES (?,?,?)", [(1.0, 710.0, 3), (2.0, 751.0, 4)])
    trend.executemany("INSERT INTO fills VALUES (?)", [(0.1,), (0.2,)])
    trend.execute("INSERT INTO funding VALUES (0.29)")
    trend.commit()
    try:
        rows = {r.name: r for r in pc.collect(tmp_path, 700.0)}
    finally:
        for db in (legacy, scalp, trend):
            db.close()
    assert set(rows) == {"bot:conservative", "scalp", "trend_ls"}   # trend_lo db missing -> skipped
    assert rows["bot:conservative"].equity == pytest.approx(703.0)
    assert rows["scalp"].equity == pytest.approx(696.0)
    assert (rows["trend_ls"].equity, rows["trend_ls"].trades, rows["trend_ls"].open_pos) == (751.0, 2, 4)
    assert "fees $0.30 funding $+0.29" in rows["trend_ls"].note


def test_main_prints_table_on_stdout(tmp_path, capsys, caplog):
    with closing(_scalper(tmp_path / pc.SCALPER_DB, [7.0])):
        pc.main(["--data", str(tmp_path)])
    out = capsys.readouterr().out
    assert "version" in out and "scalp" in out and "+1.00%" in out
    assert "missing" not in out and "missing" in caplog.text       # diagnostics stay off stdout
