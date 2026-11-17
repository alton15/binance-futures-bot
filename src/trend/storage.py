"""SQLite persistence for the trend paper account (restart safe)."""

from __future__ import annotations

import logging
from pathlib import Path
from types import MappingProxyType

import aiosqlite

from src.trend.book import Book, Holding

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    symbol TEXT PRIMARY KEY, qty REAL NOT NULL, entry_price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, symbol TEXT NOT NULL,
    dqty REAL NOT NULL, price REAL NOT NULL, fee REAL NOT NULL, realized REAL NOT NULL,
    target_weight REAL NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS funding (
    ts REAL NOT NULL, symbol TEXT NOT NULL, rate REAL NOT NULL, amount REAL NOT NULL,
    PRIMARY KEY (symbol, ts)
);
CREATE TABLE IF NOT EXISTS equity (
    ts REAL NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL, gross REAL NOT NULL,
    n_pos INTEGER NOT NULL, peak REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS targets (
    ts REAL NOT NULL, symbol TEXT NOT NULL, signal REAL NOT NULL, vol REAL NOT NULL,
    weight REAL NOT NULL, PRIMARY KEY (ts, symbol)
);
"""


class TrendStorage:
    def __init__(self, path: Path) -> None:
        self._path = path
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
            raise RuntimeError("TrendStorage not opened")
        return self._db

    async def get_meta(self, key: str) -> str | None:
        rows = await self.db.execute_fetchall("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0][0] if rows else None

    async def set_meta(self, key: str, value: str) -> None:
        await self.db.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        await self.db.commit()

    async def load_book(self, initial_capital: float) -> Book:
        cash = await self.get_meta("cash")
        rows = await self.db.execute_fetchall("SELECT symbol, qty, entry_price FROM holdings")
        holdings = {s: Holding(q, e) for s, q, e in rows}
        return Book(float(cash) if cash is not None else initial_capital, MappingProxyType(holdings))

    async def save_book(self, book: Book) -> None:
        """Replace the persisted book atomically."""
        db = self.db
        await db.execute("BEGIN")
        try:
            await db.execute("DELETE FROM holdings")
            await db.executemany("INSERT INTO holdings(symbol, qty, entry_price) VALUES(?, ?, ?)",
                                 [(s, h.qty, h.entry_price) for s, h in book.holdings.items()])
            await db.execute("INSERT INTO meta(key, value) VALUES('cash', ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (repr(book.cash),))
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def record_fill(self, ts: float, symbol: str, dqty: float, price: float, fee: float,
                          realized: float, target_weight: float, reason: str) -> None:
        await self.db.execute(
            "INSERT INTO fills(ts, symbol, dqty, price, fee, realized, target_weight, reason) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)", (ts, symbol, dqty, price, fee, realized, target_weight, reason))
        await self.db.commit()

    async def record_funding(self, ts: float, symbol: str, rate: float, amount: float) -> bool:
        """Idempotent per (symbol, settlement ts). Returns False if already booked."""
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO funding(ts, symbol, rate, amount) VALUES(?, ?, ?, ?)",
            (ts, symbol, rate, amount))
        await self.db.commit()
        return cur.rowcount > 0

    async def record_targets(self, ts: float, rows: list[tuple[str, float, float, float]]) -> None:
        await self.db.executemany(
            "INSERT OR REPLACE INTO targets(ts, symbol, signal, vol, weight) VALUES(?, ?, ?, ?, ?)",
            [(ts, *r) for r in rows])
        await self.db.commit()

    async def snapshot_equity(self, ts: float, equity: float, cash: float, gross: float,
                              n_pos: int, peak: float) -> None:
        await self.db.execute("INSERT INTO equity(ts, equity, cash, gross, n_pos, peak) VALUES(?, ?, ?, ?, ?, ?)",
                              (ts, equity, cash, gross, n_pos, peak))
        await self.db.commit()

    async def peak_equity(self) -> float | None:
        """Latest persisted peak (a manual reset writes peak = equity)."""
        rows = await self.db.execute_fetchall("SELECT peak FROM equity ORDER BY ts DESC, rowid DESC LIMIT 1")
        return float(rows[0][0]) if rows else None
