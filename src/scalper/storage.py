"""SQLite persistence: one long-lived WAL connection, small schema."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

import aiosqlite

from src.scalper.types import ClosedTrade, Position, Side, Signal

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, symbol TEXT NOT NULL, side INTEGER NOT NULL,
    price REAL NOT NULL, move_bps REAL NOT NULL, z REAL NOT NULL,
    outcome TEXT NOT NULL, mode TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, side INTEGER NOT NULL, qty REAL NOT NULL,
    entry_price REAL NOT NULL, tp_price REAL NOT NULL, sl_price REAL NOT NULL,
    leverage INTEGER NOT NULL, entry_fee REAL NOT NULL, opened_ts REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    exit_price REAL, exit_fee REAL, closed_ts REAL, reason TEXT, net_pnl REAL,
    mode TEXT NOT NULL, meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_pos_status ON positions(mode, status);
CREATE TABLE IF NOT EXISTS equity (
    ts REAL NOT NULL, mode TEXT NOT NULL, equity REAL NOT NULL, peak REAL NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path, mode: str) -> None:
        self._path = path
        self._mode = mode
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage not opened")
        return self._db

    async def log_signal(self, sig: Signal, outcome: str) -> None:
        await self.db.execute(
            "INSERT INTO signals (ts, symbol, side, price, move_bps, z, outcome, mode) VALUES (?,?,?,?,?,?,?,?)",
            (sig.ts, sig.symbol, int(sig.side), sig.price, sig.move_bps, sig.z, outcome, self._mode),
        )
        await self.db.commit()

    async def open_position(self, pos: Position, meta: dict | None = None) -> int:
        cur = await self.db.execute(
            "INSERT INTO positions (symbol, side, qty, entry_price, tp_price, sl_price, leverage, entry_fee,"
            " opened_ts, mode, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pos.symbol, int(pos.side), pos.qty, pos.entry_price, pos.tp_price, pos.sl_price,
             pos.leverage, pos.entry_fee, pos.opened_ts, self._mode, json.dumps(meta or {})),
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def close_position(self, db_id: int, t: ClosedTrade) -> bool:
        """Idempotent: only an open row can be closed. Returns True if it closed."""
        cur = await self.db.execute(
            "UPDATE positions SET status='closed', exit_price=?, exit_fee=?, closed_ts=?, reason=?, net_pnl=?"
            " WHERE id=? AND status='open'",
            (t.exit_price, t.exit_fee, t.closed_ts, t.reason.value, t.net_pnl, db_id),
        )
        await self.db.commit()
        if cur.rowcount != 1:
            logger.error("close_position: row %d was not open (double close prevented)", db_id)
        return cur.rowcount == 1

    async def open_positions(self) -> list[tuple[int, Position]]:
        rows = await self.db.execute_fetchall(
            "SELECT id, symbol, side, qty, entry_price, tp_price, sl_price, opened_ts, entry_fee, leverage"
            " FROM positions WHERE mode=? AND status='open'", (self._mode,),
        )
        return [(r[0], Position(r[0], r[1], Side(r[2]), r[3], r[4], r[5], r[6], r[7], r[8], r[9])) for r in rows]

    async def realized_pnl(self, since_ts: float | None = None) -> float:
        """Sum of closed net PnL, optionally only for trades closed at/after `since_ts`."""
        rows = await self.db.execute_fetchall(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM positions WHERE mode=? AND status='closed'"
            " AND closed_ts >= ?", (self._mode, since_ts if since_ts is not None else float("-inf")),
        )
        return float(rows[0][0])

    async def peak_equity(self) -> float | None:
        """Peak from the latest snapshot (a manual drawdown reset writes peak=equity)."""
        rows = await self.db.execute_fetchall(
            "SELECT peak FROM equity WHERE mode=? ORDER BY ts DESC, rowid DESC LIMIT 1", (self._mode,))
        return float(rows[0][0]) if rows else None

    async def record_closed(self, t: ClosedTrade, meta: dict | None = None) -> int:
        """Fallback when the open row was never written: insert it, then close it."""
        db_id = await self.open_position(t.position, (meta or {}) | {"recovered": True})
        await self.close_position(db_id, t)
        return db_id

    async def snapshot_equity(self, ts: float, equity: float, peak: float) -> None:
        await self.db.execute("INSERT INTO equity (ts, mode, equity, peak) VALUES (?,?,?,?)",
                              (ts, self._mode, equity, peak))
        await self.db.commit()

    async def stats(self) -> dict:
        rows = await self.db.execute_fetchall(
            "SELECT COUNT(*), COALESCE(SUM(net_pnl),0), COALESCE(SUM(net_pnl>0),0) FROM positions"
            " WHERE mode=? AND status='closed'", (self._mode,),
        )
        n, pnl, wins = rows[0]
        return {"trades": n, "net_pnl": float(pnl), "win_rate": (wins / n) if n else 0.0}


def position_meta(sig: Signal) -> dict:
    return {"signal": asdict(sig) | {"side": int(sig.side)}}
