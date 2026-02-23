"""SQLite models - coins, signals, trades, positions, orders, PnL, funding, indicators."""

import aiosqlite
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "futures_bot.db"


async def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Initialize all database tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS coins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                base_asset TEXT,
                volume_24h REAL DEFAULT 0,
                volatility_24h REAL DEFAULT 0,
                spread REAL DEFAULT 0,
                funding_rate REAL DEFAULT 0,
                scan_score REAL DEFAULT 0,
                last_scanned_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                strength REAL NOT NULL,
                confirming_count INTEGER DEFAULT 0,
                timeframe TEXT DEFAULT '1h',
                indicator_details TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                size REAL NOT NULL,
                cost REAL NOT NULL,
                leverage INTEGER NOT NULL DEFAULT 1,
                margin REAL NOT NULL DEFAULT 0,
                order_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                fill_price REAL,
                fill_size REAL,
                is_paper INTEGER NOT NULL DEFAULT 1,
                signal_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                filled_at TEXT,
                FOREIGN KEY (signal_id) REFERENCES signals(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL,
                mark_price REAL,
                size REAL NOT NULL,
                cost REAL NOT NULL,
                leverage INTEGER NOT NULL DEFAULT 1,
                margin REAL NOT NULL DEFAULT 0,
                liquidation_price REAL,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                funding_paid REAL DEFAULT 0,
                sl_price REAL,
                tp_price REAL,
                trailing_stop_pct REAL,
                trailing_high REAL,
                trailing_low REAL,
                status TEXT NOT NULL DEFAULT 'open',
                exit_reason TEXT,
                trade_id INTEGER,
                is_paper INTEGER NOT NULL DEFAULT 1,
                opened_at TEXT DEFAULT (datetime('now')),
                closed_at TEXT,
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                position_id INTEGER,
                order_type TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL,
                size REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                exchange_order_id TEXT,
                is_paper INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                filled_at TEXT,
                FOREIGN KEY (position_id) REFERENCES positions(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pnl_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                total_capital REAL NOT NULL,
                daily_pnl REAL NOT NULL DEFAULT 0,
                daily_pnl_pct REAL NOT NULL DEFAULT 0,
                cumulative_pnl REAL NOT NULL DEFAULT 0,
                cumulative_pnl_pct REAL NOT NULL DEFAULT 0,
                peak_capital REAL NOT NULL DEFAULT 0,
                drawdown_pct REAL NOT NULL DEFAULT 0,
                open_positions INTEGER DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                is_paper INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS funding_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                position_id INTEGER,
                funding_rate REAL NOT NULL,
                payment REAL NOT NULL,
                timestamp TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (position_id) REFERENCES positions(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS indicator_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL DEFAULT '1h',
                rsi REAL,
                macd REAL,
                macd_signal REAL,
                macd_hist REAL,
                bb_upper REAL,
                bb_mid REAL,
                bb_lower REAL,
                ema_fast REAL,
                ema_mid REAL,
                ema_slow REAL,
                atr REAL,
                adx REAL,
                stoch_k REAL,
                stoch_d REAL,
                volume REAL,
                close_price REAL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Indexes
        await db.execute("CREATE INDEX IF NOT EXISTS idx_coins_symbol ON coins(symbol)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(is_paper, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_positions_paper ON positions(is_paper, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pnl_date ON pnl_snapshots(date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pnl_paper ON pnl_snapshots(is_paper)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_funding_symbol ON funding_payments(symbol)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_indicators_symbol ON indicator_snapshots(symbol, timeframe)")
        await db.commit()


# -- Coins CRUD ----------------------------------------------------


async def upsert_coin(
    symbol: str,
    base_asset: str = "",
    volume_24h: float = 0,
    volatility_24h: float = 0,
    spread: float = 0,
    funding_rate: float = 0,
    scan_score: float = 0,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Insert or update a coin."""
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """INSERT INTO coins
               (symbol, base_asset, volume_24h, volatility_24h, spread,
                funding_rate, scan_score, last_scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(symbol) DO UPDATE SET
                 base_asset=excluded.base_asset, volume_24h=excluded.volume_24h,
                 volatility_24h=excluded.volatility_24h, spread=excluded.spread,
                 funding_rate=excluded.funding_rate, scan_score=excluded.scan_score,
                 last_scanned_at=datetime('now')""",
            (symbol, base_asset, volume_24h, volatility_24h, spread,
             funding_rate, scan_score),
        )
        await db.commit()


async def get_coin(symbol: str, db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    """Get a coin by symbol."""
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM coins WHERE symbol = ?", (symbol,))
        row = await cursor.fetchone()
        return dict(row) if row else None


# -- Signals CRUD --------------------------------------------------


async def save_signal(
    symbol: str,
    direction: str,
    strength: float,
    confirming_count: int = 0,
    timeframe: str = "1h",
    indicator_details: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Save a trading signal. Returns signal ID."""
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            """INSERT INTO signals
               (symbol, direction, strength, confirming_count, timeframe, indicator_details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, direction, strength, confirming_count, timeframe, indicator_details),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def was_recently_analyzed(
    symbol: str,
    cooldown_hours: float = 4.0,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Check if a symbol was analyzed within the cooldown period."""
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            """SELECT 1 FROM signals
               WHERE symbol=? AND created_at > datetime('now', ?)
               LIMIT 1""",
            (symbol, f"-{cooldown_hours} hours"),
        )
        return await cursor.fetchone() is not None


# -- Trades CRUD ---------------------------------------------------


async def save_trade(
    symbol: str,
    direction: str,
    entry_price: float,
    size: float,
    cost: float,
    leverage: int = 1,
    margin: float = 0,
    order_id: str = "",
    status: str = "pending",
    is_paper: bool = True,
    signal_id: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Save a trade record. Returns trade ID."""
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            """INSERT INTO trades
               (symbol, direction, entry_price, size, cost, leverage, margin,
                order_id, status, is_paper, signal_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, direction, entry_price, size, cost, leverage, margin,
             order_id, status, int(is_paper), signal_id),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def update_trade_status(
    trade_id: int,
    status: str,
    fill_price: float | None = None,
    fill_size: float | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Update a trade's status and fill info."""
    async with aiosqlite.connect(str(db_path)) as db:
        if fill_price is not None:
            await db.execute(
                """UPDATE trades SET status=?, fill_price=?, fill_size=?,
                   filled_at=datetime('now') WHERE id=?""",
                (status, fill_price, fill_size, trade_id),
            )
        else:
            await db.execute(
                "UPDATE trades SET status=? WHERE id=?", (status, trade_id)
            )
        await db.commit()


# -- Positions CRUD ------------------------------------------------


async def open_position(
    symbol: str,
    direction: str,
    entry_price: float,
    size: float,
    cost: float,
    leverage: int = 1,
    margin: float = 0,
    liquidation_price: float | None = None,
    sl_price: float | None = None,
    tp_price: float | None = None,
    trailing_stop_pct: float | None = None,
    trade_id: int | None = None,
    is_paper: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Open a new position. Returns position ID."""
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            """INSERT INTO positions
               (symbol, direction, entry_price, current_price, size, cost,
                leverage, margin, liquidation_price, sl_price, tp_price,
                trailing_stop_pct, trailing_high, trailing_low,
                trade_id, is_paper)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, direction, entry_price, entry_price, size, cost,
             leverage, margin, liquidation_price, sl_price, tp_price,
             trailing_stop_pct,
             entry_price if direction == "LONG" else None,
             entry_price if direction == "SHORT" else None,
             trade_id, int(is_paper)),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def close_position(
    position_id: int,
    realized_pnl: float,
    exit_reason: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Close a position."""
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """UPDATE positions SET status='closed', realized_pnl=?,
               exit_reason=?, closed_at=datetime('now') WHERE id=?""",
            (realized_pnl, exit_reason, position_id),
        )
        await db.commit()


async def get_open_positions(
    is_paper: bool = True, db_path: Path = DEFAULT_DB_PATH
) -> list[dict]:
    """Get all open positions."""
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM positions WHERE status='open' AND is_paper=?",
            (int(is_paper),),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def update_position_price(
    position_id: int,
    current_price: float,
    unrealized_pnl: float,
    mark_price: float | None = None,
    trailing_high: float | None = None,
    trailing_low: float | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Update a position's current price and unrealized P&L."""
    async with aiosqlite.connect(str(db_path)) as db:
        fields = "current_price=?, unrealized_pnl=?"
        params: list[Any] = [current_price, unrealized_pnl]
        if mark_price is not None:
            fields += ", mark_price=?"
            params.append(mark_price)
        if trailing_high is not None:
            fields += ", trailing_high=?"
            params.append(trailing_high)
        if trailing_low is not None:
            fields += ", trailing_low=?"
            params.append(trailing_low)
        params.append(position_id)
        await db.execute(f"UPDATE positions SET {fields} WHERE id=?", params)
        await db.commit()


async def update_position_funding(
    position_id: int,
    funding_paid: float,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Add accumulated funding payment to a position."""
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "UPDATE positions SET funding_paid = funding_paid + ? WHERE id=?",
            (funding_paid, position_id),
        )
        await db.commit()


async def has_position_for_symbol(
    symbol: str,
    is_paper: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Check if there's already an open position for this symbol."""
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT 1 FROM positions WHERE symbol=? AND status='open' AND is_paper=?",
            (symbol, int(is_paper)),
        )
        return await cursor.fetchone() is not None


# -- Orders CRUD ---------------------------------------------------


async def save_order(
    symbol: str,
    position_id: int,
    order_type: str,
    side: str,
    size: float,
    price: float | None = None,
    exchange_order_id: str = "",
    is_paper: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Save an order (SL/TP/trailing). Returns order ID."""
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            """INSERT INTO orders
               (symbol, position_id, order_type, side, price, size,
                exchange_order_id, is_paper)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, position_id, order_type, side, price, size,
             exchange_order_id, int(is_paper)),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


# -- PnL Snapshots -------------------------------------------------


async def save_pnl_snapshot(
    total_capital: float,
    daily_pnl: float = 0,
    daily_pnl_pct: float = 0,
    cumulative_pnl: float = 0,
    cumulative_pnl_pct: float = 0,
    peak_capital: float = 0,
    drawdown_pct: float = 0,
    open_positions: int = 0,
    total_trades: int = 0,
    win_rate: float = 0,
    is_paper: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Save a daily P&L snapshot."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """INSERT INTO pnl_snapshots
               (date, total_capital, daily_pnl, daily_pnl_pct,
                cumulative_pnl, cumulative_pnl_pct, peak_capital,
                drawdown_pct, open_positions, total_trades, win_rate, is_paper)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (today, total_capital, daily_pnl, daily_pnl_pct,
             cumulative_pnl, cumulative_pnl_pct, peak_capital,
             drawdown_pct, open_positions, total_trades, win_rate, int(is_paper)),
        )
        await db.commit()


async def get_peak_capital(
    is_paper: bool = True, db_path: Path = DEFAULT_DB_PATH
) -> float:
    """Get historical peak capital for drawdown calculation."""
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(peak_capital), 0) FROM pnl_snapshots WHERE is_paper=?",
            (int(is_paper),),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0.0


async def get_today_realized_pnl(
    is_paper: bool = True, db_path: Path = DEFAULT_DB_PATH
) -> float:
    """Get total realized P&L for today's closed positions."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            """SELECT COALESCE(SUM(realized_pnl), 0) FROM positions
               WHERE DATE(closed_at)=? AND is_paper=?""",
            (today, int(is_paper)),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0.0


# -- Funding Payments ----------------------------------------------


async def save_funding_payment(
    symbol: str,
    position_id: int,
    funding_rate: float,
    payment: float,
    timestamp: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Record a funding fee payment."""
    if not timestamp:
        timestamp = datetime.now().isoformat()
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """INSERT INTO funding_payments
               (symbol, position_id, funding_rate, payment, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (symbol, position_id, funding_rate, payment, timestamp),
        )
        await db.commit()


# -- Indicator Snapshots -------------------------------------------


async def save_indicator_snapshot(
    symbol: str,
    timeframe: str = "1h",
    close_price: float | None = None,
    volume: float | None = None,
    rsi: float | None = None,
    macd: float | None = None,
    macd_signal: float | None = None,
    macd_hist: float | None = None,
    bb_upper: float | None = None,
    bb_mid: float | None = None,
    bb_lower: float | None = None,
    ema_fast: float | None = None,
    ema_mid: float | None = None,
    ema_slow: float | None = None,
    atr: float | None = None,
    adx: float | None = None,
    stoch_k: float | None = None,
    stoch_d: float | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Save an indicator snapshot for analysis history."""
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """INSERT INTO indicator_snapshots
               (symbol, timeframe, close_price, volume, rsi, macd, macd_signal,
                macd_hist, bb_upper, bb_mid, bb_lower, ema_fast, ema_mid, ema_slow,
                atr, adx, stoch_k, stoch_d)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, timeframe, close_price, volume, rsi, macd, macd_signal,
             macd_hist, bb_upper, bb_mid, bb_lower, ema_fast, ema_mid, ema_slow,
             atr, adx, stoch_k, stoch_d),
        )
        await db.commit()


# -- Utility -------------------------------------------------------


async def get_trading_stats(
    is_paper: bool = True, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any]:
    """Get overall trading statistics."""
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM trades WHERE is_paper=?", (int(is_paper),)
        )
        total_trades = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM positions WHERE status='open' AND is_paper=?",
            (int(is_paper),),
        )
        open_count = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """SELECT COUNT(*), SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
               FROM positions WHERE status='closed' AND is_paper=?""",
            (int(is_paper),),
        )
        row = await cursor.fetchone()
        closed = row[0] if row else 0
        wins = row[1] if row and row[1] else 0

        cursor = await db.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE status='closed' AND is_paper=?",
            (int(is_paper),),
        )
        total_pnl = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """SELECT COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0),
                      COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 0),
                      COALESCE(MAX(realized_pnl), 0),
                      COALESCE(MIN(realized_pnl), 0),
                      COALESCE(AVG(realized_pnl), 0)
               FROM positions WHERE status='closed' AND is_paper=?""",
            (int(is_paper),),
        )
        pnl_row = await cursor.fetchone()
        total_gains = pnl_row[0] if pnl_row else 0
        total_losses = pnl_row[1] if pnl_row else 0
        best_trade = pnl_row[2] if pnl_row else 0
        worst_trade = pnl_row[3] if pnl_row else 0
        avg_pnl = pnl_row[4] if pnl_row else 0

        cursor = await db.execute(
            "SELECT COALESCE(SUM(unrealized_pnl), 0) FROM positions WHERE status='open' AND is_paper=?",
            (int(is_paper),),
        )
        unrealized = (await cursor.fetchone())[0]

        today = datetime.now().strftime("%Y-%m-%d")
        cursor = await db.execute(
            "SELECT COUNT(*) FROM trades WHERE DATE(created_at)=? AND is_paper=?",
            (today, int(is_paper)),
        )
        today_trades = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """SELECT COALESCE(SUM(realized_pnl), 0),
                      COUNT(*),
                      SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
               FROM positions WHERE DATE(closed_at)=? AND status='closed' AND is_paper=?""",
            (today, int(is_paper)),
        )
        today_row = await cursor.fetchone()
        today_pnl = today_row[0] if today_row else 0
        today_closed = today_row[1] if today_row else 0
        today_wins = today_row[2] if today_row and today_row[2] else 0

        # Total margin in use
        cursor = await db.execute(
            "SELECT COALESCE(SUM(margin), 0) FROM positions WHERE status='open' AND is_paper=?",
            (int(is_paper),),
        )
        total_margin = (await cursor.fetchone())[0]

        # Total funding paid
        cursor = await db.execute(
            "SELECT COALESCE(SUM(funding_paid), 0) FROM positions WHERE is_paper=?",
            (int(is_paper),),
        )
        total_funding = (await cursor.fetchone())[0]

        return {
            "total_trades": total_trades,
            "open_positions": open_count,
            "closed_positions": closed,
            "wins": wins,
            "losses": closed - wins,
            "win_rate": round(wins / closed, 4) if closed > 0 else 0,
            "total_realized_pnl": round(total_pnl, 4),
            "total_gains": round(total_gains, 4),
            "total_losses": round(total_losses, 4),
            "best_trade": round(best_trade, 4),
            "worst_trade": round(worst_trade, 4),
            "avg_pnl": round(avg_pnl, 4),
            "unrealized_pnl": round(unrealized, 4),
            "total_margin_in_use": round(total_margin, 4),
            "total_funding_paid": round(total_funding, 4),
            "today_trades": today_trades,
            "today_pnl": round(today_pnl, 4),
            "today_closed": today_closed,
            "today_wins": today_wins,
            "today_win_rate": round(today_wins / today_closed, 4) if today_closed > 0 else 0,
        }


async def get_recent_trades(
    is_paper: bool = True,
    limit: int = 10,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """Get recent trades."""
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT t.*, p.realized_pnl, p.status AS pos_status
               FROM trades t
               LEFT JOIN positions p ON t.id = p.trade_id
               WHERE t.is_paper=?
               ORDER BY t.created_at DESC LIMIT ?""",
            (int(is_paper), limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_risk_summary(
    is_paper: bool = True, db_path: Path = DEFAULT_DB_PATH
) -> dict:
    """Get current risk exposure summary."""
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(margin), 0), COUNT(*) FROM positions WHERE status='open' AND is_paper=?",
            (int(is_paper),),
        )
        row = await cursor.fetchone()
        total_margin = row[0] if row else 0
        open_count = row[1] if row else 0

        cursor = await db.execute(
            "SELECT COALESCE(MAX(margin), 0) FROM positions WHERE status='open' AND is_paper=?",
            (int(is_paper),),
        )
        max_margin = (await cursor.fetchone())[0]

        today = datetime.now().strftime("%Y-%m-%d")
        cursor = await db.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE DATE(closed_at)=? AND is_paper=?",
            (today, int(is_paper)),
        )
        today_pnl = (await cursor.fetchone())[0]

        # Total notional exposure
        cursor = await db.execute(
            "SELECT COALESCE(SUM(cost), 0) FROM positions WHERE status='open' AND is_paper=?",
            (int(is_paper),),
        )
        total_exposure = (await cursor.fetchone())[0]

        return {
            "total_margin": round(total_margin, 4),
            "total_exposure": round(total_exposure, 4),
            "open_positions": open_count,
            "max_single_margin": round(max_margin, 4),
            "today_realized_pnl": round(today_pnl, 4),
        }
