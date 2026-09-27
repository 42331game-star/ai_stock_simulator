import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional


def _safe_json_loads(raw: Any, default: Any):
    """Parse a JSON column, returning `default` instead of raising on corruption."""
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return default
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default
    if type(value) is not type(default):
        return default
    return value


class SimulationDB:
    def __init__(self, db_path: str = "simulator.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            # WAL lets the simulation process and the dashboard process share one file.
            # busy_timeout stops "database is locked" from transient writer contention.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    cash REAL DEFAULT 10000.0,
                    salary REAL DEFAULT 0.0,
                    cost_multiplier REAL DEFAULT 1.0,
                    holdings TEXT DEFAULT '{}',
                    trading_memory TEXT DEFAULT '[]',
                    social_memory TEXT DEFAULT '[]',
                    policy_memory TEXT DEFAULT '[]',
                    bulletin_memory TEXT DEFAULT '[]',
                    fundamental_memory TEXT DEFAULT '[]',
                    episodic_summary TEXT DEFAULT '{}',
                    belief_memory TEXT DEFAULT '{}',
                    relationship_memory TEXT DEFAULT '{}',
                    last_updated INTEGER
                )
                """
            )

            for column_name, default_value in [
                ("is_active", "1"),
                ("trading_memory", "'[]'"),
                ("social_memory", "'[]'"),
                ("policy_memory", "'[]'"),
                ("bulletin_memory", "'[]'"),
                ("fundamental_memory", "'[]'"),
                ("episodic_summary", "'{}'"),
                ("belief_memory", "'{}'"),
                ("relationship_memory", "'{}'"),
            ]:
                try:
                    column_type = "INTEGER" if column_name == "is_active" else "TEXT"
                    cursor.execute(f"ALTER TABLE agents ADD COLUMN {column_name} {column_type} DEFAULT {default_value}")
                except sqlite3.OperationalError:
                    pass

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stocks (
                    symbol TEXT PRIMARY KEY,
                    price REAL NOT NULL,
                    beta REAL DEFAULT 1.0,
                    eps REAL DEFAULT 0.0,
                    shares_outstanding REAL DEFAULT 1000000.0,
                    forward_eps REAL DEFAULT 0.0,
                    growth_rate REAL DEFAULT 0.0,
                    target_pe REAL DEFAULT 10.0,
                    fair_value REAL DEFAULT 0.0,
                    liquidation_value REAL DEFAULT 0.0,
                    dividend_per_share REAL DEFAULT 0.0,
                    risk_score REAL DEFAULT 0.5,
                    sentiment_beta REAL DEFAULT 1.0,
                    growth_regime TEXT DEFAULT 'Stable'
                )
                """
            )

            for column_name, column_type, default_value in [
                ("shares_outstanding", "REAL", "1000000.0"),
                ("forward_eps", "REAL", "0.0"),
                ("growth_rate", "REAL", "0.0"),
                ("target_pe", "REAL", "10.0"),
                ("fair_value", "REAL", "0.0"),
                ("liquidation_value", "REAL", "0.0"),
                ("dividend_per_share", "REAL", "0.0"),
                ("risk_score", "REAL", "0.5"),
                ("sentiment_beta", "REAL", "1.0"),
                ("growth_regime", "TEXT", "'Stable'"),
            ]:
                try:
                    cursor.execute(
                        f"ALTER TABLE stocks ADD COLUMN {column_name} {column_type} DEFAULT {default_value}"
                    )
                except sqlite3.OperationalError:
                    pass

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS market_history (
                    tick INTEGER,
                    symbol TEXT,
                    price REAL,
                    volume INTEGER DEFAULT 0,
                    day INTEGER DEFAULT 1,
                    day_tick INTEGER DEFAULT 0,
                    phase TEXT DEFAULT 'OPEN',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tick, symbol)
                )
                """
            )

            for column_name, column_type, default_value in [
                ("volume", "INTEGER", "0"),
                ("day", "INTEGER", "1"),
                ("day_tick", "INTEGER", "0"),
                ("phase", "TEXT", "'OPEN'"),
            ]:
                try:
                    cursor.execute(
                        f"ALTER TABLE market_history ADD COLUMN {column_name} {column_type} DEFAULT {default_value}"
                    )
                except sqlite3.OperationalError:
                    pass

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    tick INTEGER NOT NULL,
                    day INTEGER DEFAULT 1,
                    day_tick INTEGER DEFAULT 0,
                    phase TEXT DEFAULT 'UNKNOWN',
                    source TEXT DEFAULT 'snapshot',
                    cash REAL NOT NULL,
                    stock_value REAL NOT NULL,
                    total_assets REAL NOT NULL,
                    holdings TEXT DEFAULT '{}',
                    note TEXT DEFAULT '',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_asset_history_agent_tick ON asset_history(agent_id, tick, id)"
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_asset_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tick INTEGER NOT NULL,
                    day INTEGER DEFAULT 1,
                    day_tick INTEGER DEFAULT 0,
                    agent_id TEXT NOT NULL,
                    stock TEXT NOT NULL,
                    action TEXT NOT NULL,
                    requested_amount INTEGER DEFAULT 0,
                    executed_amount INTEGER DEFAULT 0,
                    price REAL NOT NULL,
                    cash_before REAL NOT NULL,
                    cash_after REAL NOT NULL,
                    stock_value_before REAL NOT NULL,
                    stock_value_after REAL NOT NULL,
                    total_assets_before REAL NOT NULL,
                    total_assets_after REAL NOT NULL,
                    holdings_before TEXT DEFAULT '{}',
                    holdings_after TEXT DEFAULT '{}',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_trade_asset_log_agent_tick ON trade_asset_log(agent_id, tick, id)"
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS market_round_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tick INTEGER NOT NULL,
                    day INTEGER DEFAULT 1,
                    day_tick INTEGER DEFAULT 0,
                    phase TEXT DEFAULT 'OPEN',
                    symbol TEXT NOT NULL,
                    buy_volume INTEGER DEFAULT 0,
                    sell_volume INTEGER DEFAULT 0,
                    matched_volume INTEGER DEFAULT 0,
                    open_price REAL DEFAULT 0,
                    close_price REAL DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_market_round_stats_symbol_tick ON market_round_stats(symbol, tick, id)"
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS forum_posts (
                    post_id INTEGER PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sentiment REAL DEFAULT 0.0,
                    is_hot INTEGER DEFAULT 0,
                    parent_id INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_forum_posts_timestamp ON forum_posts(post_id, timestamp)"
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day INTEGER DEFAULT 1,
                    tick INTEGER DEFAULT 0,
                    day_tick INTEGER DEFAULT 0,
                    policy_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    metrics TEXT DEFAULT '{}',
                    actions TEXT DEFAULT '{}',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_policy_history_tick ON policy_history(tick, id)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bulletin_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day INTEGER DEFAULT 1,
                    tick INTEGER DEFAULT 0,
                    day_tick INTEGER DEFAULT 0,
                    phase TEXT DEFAULT 'UNKNOWN',
                    author_id TEXT DEFAULT 'BulletinBoard',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance REAL DEFAULT 0.5,
                    expires_at DATETIME,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_bulletin_history_tick ON bulletin_history(tick, id)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day INTEGER NOT NULL,
                    tick INTEGER NOT NULL,
                    day_tick INTEGER NOT NULL,
                    phase TEXT DEFAULT 'CLOSED',
                    health_status TEXT DEFAULT 'ok',
                    summary TEXT DEFAULT '',
                    stats TEXT DEFAULT '{}',
                    issues TEXT DEFAULT '[]',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_audit_day_tick ON daily_audit(day, tick, id)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_split_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day INTEGER DEFAULT 1,
                    tick INTEGER DEFAULT 0,
                    day_tick INTEGER DEFAULT 0,
                    phase TEXT DEFAULT 'OPEN',
                    symbol TEXT NOT NULL,
                    split_ratio INTEGER NOT NULL,
                    old_price REAL NOT NULL,
                    new_price REAL NOT NULL,
                    source TEXT DEFAULT 'manual',
                    reason TEXT DEFAULT '',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_stock_split_history_symbol_tick ON stock_split_history(symbol, tick, id)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS fundamental_event_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day INTEGER DEFAULT 1,
                    tick INTEGER DEFAULT 0,
                    day_tick INTEGER DEFAULT 0,
                    phase TEXT DEFAULT 'CLOSED',
                    symbol TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    impact_score REAL DEFAULT 0.0,
                    details TEXT DEFAULT '{}',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_fundamental_event_history_symbol_tick ON fundamental_event_history(symbol, tick, id)"
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_tick INTEGER DEFAULT 0,
                    day_tick INTEGER DEFAULT 0,
                    day_count INTEGER DEFAULT 1,
                    phase TEXT DEFAULT 'CLOSED',
                    base_living_cost REAL DEFAULT 400.0,
                    active_policy_type TEXT,
                    active_policy_title TEXT,
                    active_policy_summary TEXT,
                    active_policy_day INTEGER,
                    active_policy_issued_at TEXT,
                    active_policy_expires_at TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            try:
                cursor.execute("ALTER TABLE runtime_state ADD COLUMN base_living_cost REAL DEFAULT 400.0")
            except sqlite3.OperationalError:
                pass
            for column_name, column_sql in [
                ("active_policy_type", "TEXT"),
                ("active_policy_title", "TEXT"),
                ("active_policy_summary", "TEXT"),
                ("active_policy_day", "INTEGER"),
                ("active_policy_issued_at", "TEXT"),
                ("active_policy_expires_at", "TEXT"),
            ]:
                try:
                    cursor.execute(f"ALTER TABLE runtime_state ADD COLUMN {column_name} {column_sql}")
                except sqlite3.OperationalError:
                    pass
            cursor.execute(
                """
                INSERT OR IGNORE INTO runtime_state (
                    id, current_tick, day_tick, day_count, phase, base_living_cost,
                    active_policy_type, active_policy_title, active_policy_summary, active_policy_day,
                    active_policy_issued_at, active_policy_expires_at
                )
                VALUES (1, 0, 0, 1, 'CLOSED', 400.0, NULL, NULL, NULL, NULL, NULL, NULL)
                """
            )

    def update_agent_cash(self, agent_id: str, amount: float):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE agents SET cash = MAX(cash + ?, 0) WHERE id = ?",
                (amount, agent_id),
            )

    def create_agent(
        self,
        agent_id: str,
        role: str,
        cash: float,
        salary: float,
        cost_multiplier: float,
        holdings: Dict[str, Any],
    ):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO agents (
                    id, role, is_active, cash, salary, cost_multiplier, holdings,
                    trading_memory, social_memory, policy_memory, bulletin_memory, fundamental_memory,
                    episodic_summary, belief_memory, relationship_memory, last_updated
                ) VALUES (?, ?, 1, ?, ?, ?, ?, '[]', '[]', '[]', '[]', '[]', '{}', '{}', '{}', strftime('%s','now'))
                """,
                (
                    agent_id,
                    role,
                    float(cash),
                    float(salary),
                    float(cost_multiplier),
                    json.dumps(holdings, ensure_ascii=False),
                ),
            )

    def get_agent_state(self, agent_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["holdings"] = _safe_json_loads(result["holdings"], {})
            return result

    def update_agent_holdings(self, agent_id: str, symbol: str, quantity: int):
        # Read and write must happen on the same connection, otherwise a
        # concurrent writer in the other process can lose this update.
        with self._get_connection() as conn:
            row = conn.execute("SELECT holdings FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if not row:
                return
            holdings = _safe_json_loads(row["holdings"], {})
            if not isinstance(holdings, dict):
                holdings = {}
            holdings[symbol] = int(holdings.get(symbol, 0)) + int(quantity)
            if holdings[symbol] < 0:
                holdings[symbol] = 0
            conn.execute(
                "UPDATE agents SET holdings = ? WHERE id = ?",
                (json.dumps(holdings, ensure_ascii=False), agent_id),
            )

    def apply_trade_settlement(
        self,
        agent_id: str,
        cash_delta: float,
        holdings_delta: Dict[str, int],
    ):
        """Apply cash and share changes in one atomic transaction.

        Cash and holdings must never be committed separately: a crash in
        between would leave money taken with no shares delivered (or the
        reverse on a sale).
        """
        with self._get_connection() as conn:
            row = conn.execute("SELECT cash, holdings FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if not row:
                return
            holdings = _safe_json_loads(row["holdings"], {})
            if not isinstance(holdings, dict):
                holdings = {}
            for symbol, delta in holdings_delta.items():
                if not delta:
                    continue
                new_total = int(holdings.get(symbol, 0)) + int(delta)
                holdings[symbol] = max(new_total, 0)
            new_cash = max(float(row["cash"]) + float(cash_delta), 0.0)
            conn.execute(
                "UPDATE agents SET cash = ?, holdings = ?, last_updated = strftime('%s','now') WHERE id = ?",
                (new_cash, json.dumps(holdings, ensure_ascii=False), agent_id),
            )

    def record_price(
        self,
        symbol: str,
        price: float,
        volume: int = 0,
        tick: int = 0,
        day: int = 1,
        day_tick: int = 0,
        phase: str = "OPEN",
    ):
        with self._get_connection() as conn:
            conn.execute("UPDATE stocks SET price = ? WHERE symbol = ?", (price, symbol))
            conn.execute(
                """
                INSERT OR REPLACE INTO market_history (tick, symbol, price, volume, day, day_tick, phase)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tick, symbol, price, volume, day, day_tick, phase),
            )

    def get_stock_price(self, symbol: str) -> Optional[float]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT price FROM stocks WHERE symbol = ?", (symbol,)).fetchone()
            return row["price"] if row else None

    def get_all_stock_prices(self) -> Dict[str, float]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT symbol, price FROM stocks").fetchall()
            return {row["symbol"]: row["price"] for row in rows}

    def get_price_history(self, symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT tick, day, day_tick, phase, price, volume
                FROM market_history
                WHERE symbol = ?
                ORDER BY tick DESC
                LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def get_market_history(self, symbol: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT tick, day, day_tick, phase, price, volume, timestamp
                FROM market_history
                WHERE symbol = ?
                ORDER BY tick DESC
                LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def init_stock(self, symbol: str, price: float, beta: float = 1.0, eps: float = 0.0):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO stocks (symbol, price, beta, eps) VALUES (?, ?, ?, ?)",
                (symbol, price, beta, eps),
            )

    def update_stock_fundamentals(self, symbol: str, fundamentals: Dict[str, Any]):
        columns = [
            "shares_outstanding",
            "forward_eps",
            "growth_rate",
            "target_pe",
            "fair_value",
            "liquidation_value",
            "dividend_per_share",
            "risk_score",
            "sentiment_beta",
            "growth_regime",
            "eps",
        ]
        assignments = []
        values: List[Any] = []
        for column in columns:
            if column not in fundamentals:
                continue
            assignments.append(f"{column} = ?")
            values.append(fundamentals[column])
        if not assignments:
            return
        values.append(symbol)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE stocks SET {', '.join(assignments)} WHERE symbol = ?",
                tuple(values),
            )

    def get_stock_catalog(self) -> Dict[str, Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT symbol, price, beta, eps, shares_outstanding, forward_eps, growth_rate,
                       target_pe, fair_value, liquidation_value, dividend_per_share,
                       risk_score, sentiment_beta, growth_regime
                FROM stocks
                """
            ).fetchall()
            return {
                row["symbol"]: {
                    "symbol": row["symbol"],
                    "price": float(row["price"]),
                    "beta": float(row["beta"]),
                    "eps": float(row["eps"]),
                    "shares_outstanding": float(row["shares_outstanding"]),
                    "forward_eps": float(row["forward_eps"]),
                    "growth_rate": float(row["growth_rate"]),
                    "target_pe": float(row["target_pe"]),
                    "fair_value": float(row["fair_value"]),
                    "liquidation_value": float(row["liquidation_value"]),
                    "dividend_per_share": float(row["dividend_per_share"]),
                    "risk_score": float(row["risk_score"]),
                    "sentiment_beta": float(row["sentiment_beta"]),
                    "growth_regime": row["growth_regime"] or "Stable",
                }
                for row in rows
            }

    def adjust_stock_fundamentals_for_split(self, symbol: str, split_ratio: int):
        if split_ratio < 2:
            return
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT shares_outstanding, forward_eps, fair_value, liquidation_value,
                       dividend_per_share, eps
                FROM stocks
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()
            if not row:
                return
            conn.execute(
                """
                UPDATE stocks
                SET shares_outstanding = ?,
                    forward_eps = ?,
                    fair_value = ?,
                    liquidation_value = ?,
                    dividend_per_share = ?,
                    eps = ?
                WHERE symbol = ?
                """,
                (
                    float(row["shares_outstanding"]) * split_ratio,
                    float(row["forward_eps"]) / split_ratio,
                    float(row["fair_value"]) / split_ratio,
                    float(row["liquidation_value"]) / split_ratio,
                    float(row["dividend_per_share"]) / split_ratio,
                    float(row["eps"]) / split_ratio,
                    symbol,
                ),
            )

    def record_stock_split_event(
        self,
        day: int,
        tick: int,
        day_tick: int,
        phase: str,
        symbol: str,
        split_ratio: int,
        old_price: float,
        new_price: float,
        source: str,
        reason: str,
    ):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO stock_split_history (
                    day, tick, day_tick, phase, symbol, split_ratio,
                    old_price, new_price, source, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day,
                    tick,
                    day_tick,
                    phase,
                    symbol,
                    int(split_ratio),
                    float(old_price),
                    float(new_price),
                    source,
                    reason,
                ),
            )

    def get_recent_stock_splits(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT day, tick, day_tick, phase, symbol, split_ratio,
                       old_price, new_price, source, reason, timestamp
                FROM stock_split_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def record_fundamental_event(
        self,
        day: int,
        tick: int,
        day_tick: int,
        phase: str,
        symbol: str,
        event_type: str,
        event_title: str,
        summary: str,
        impact_score: float,
        details: Dict[str, Any] | None = None,
    ) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO fundamental_event_history (
                    day, tick, day_tick, phase, symbol, event_type, event_title, summary, impact_score, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day,
                    tick,
                    day_tick,
                    phase,
                    symbol,
                    event_type,
                    event_title,
                    summary,
                    float(impact_score),
                    json.dumps(details or {}, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def get_recent_fundamental_events(self, limit: int = 20, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if symbol:
                rows = conn.execute(
                    """
                    SELECT day, tick, day_tick, phase, symbol, event_type, event_title,
                           summary, impact_score, details, timestamp
                    FROM fundamental_event_history
                    WHERE symbol = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (symbol, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT day, tick, day_tick, phase, symbol, event_type, event_title,
                           summary, impact_score, details, timestamp
                    FROM fundamental_event_history
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            item["details"] = _safe_json_loads(item.get("details"), {})
            result.append(item)
        return result

    def record_bulletin(
        self,
        day: int,
        tick: int,
        day_tick: int,
        phase: str,
        title: str,
        content: str,
        importance: float = 0.5,
        author_id: str = "BulletinBoard",
        expires_at: Optional[str] = None,
    ) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO bulletin_history (
                    day, tick, day_tick, phase, author_id, title, content, importance, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day,
                    tick,
                    day_tick,
                    phase,
                    author_id,
                    title,
                    content,
                    float(importance),
                    expires_at,
                ),
            )
            return int(cursor.lastrowid)

    def get_recent_bulletins(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, day, tick, day_tick, phase, author_id, title, content, importance, expires_at, timestamp
                FROM bulletin_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def record_asset_snapshot(
        self,
        agent_id: str,
        tick: int,
        day: int,
        day_tick: int,
        phase: str,
        source: str,
        cash: float,
        stock_value: float,
        total_assets: float,
        holdings: Dict[str, Any],
        note: str = "",
    ):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO asset_history (
                    agent_id, tick, day, day_tick, phase, source,
                    cash, stock_value, total_assets, holdings, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    tick,
                    day,
                    day_tick,
                    phase,
                    source,
                    cash,
                    stock_value,
                    total_assets,
                    json.dumps(holdings),
                    note,
                ),
            )

    def record_trade_asset_change(
        self,
        tick: int,
        day: int,
        day_tick: int,
        agent_id: str,
        stock: str,
        action: str,
        requested_amount: int,
        executed_amount: int,
        price: float,
        cash_before: float,
        cash_after: float,
        stock_value_before: float,
        stock_value_after: float,
        total_assets_before: float,
        total_assets_after: float,
        holdings_before: Dict[str, Any],
        holdings_after: Dict[str, Any],
    ):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO trade_asset_log (
                    tick, day, day_tick, agent_id, stock, action,
                    requested_amount, executed_amount, price,
                    cash_before, cash_after,
                    stock_value_before, stock_value_after,
                    total_assets_before, total_assets_after,
                    holdings_before, holdings_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tick,
                    day,
                    day_tick,
                    agent_id,
                    stock,
                    action,
                    requested_amount,
                    executed_amount,
                    price,
                    cash_before,
                    cash_after,
                    stock_value_before,
                    stock_value_after,
                    total_assets_before,
                    total_assets_after,
                    json.dumps(holdings_before),
                    json.dumps(holdings_after),
                ),
            )

    def get_asset_history(self, agent_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT tick, day, day_tick, phase, source, cash, stock_value, total_assets, holdings, note, timestamp
                FROM asset_history
                WHERE agent_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (agent_id, limit),
            ).fetchall()
            results = []
            for row in reversed(rows):
                item = dict(row)
                item["holdings"] = _safe_json_loads(item["holdings"], {})
                results.append(item)
            return results

    def get_recent_trade_asset_log(self, agent_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        query = """
            SELECT tick, day, day_tick, agent_id, stock, action, requested_amount, executed_amount, price,
                   cash_before, cash_after, stock_value_before, stock_value_after,
                   total_assets_before, total_assets_after, holdings_before, holdings_after, timestamp
            FROM trade_asset_log
        """
        params: List[Any] = []
        if agent_id:
            query += " WHERE agent_id = ?"
            params.append(agent_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for row in reversed(rows):
                item = dict(row)
                item["holdings_before"] = _safe_json_loads(item["holdings_before"], {})
                item["holdings_after"] = _safe_json_loads(item["holdings_after"], {})
                results.append(item)
            return results

    def record_market_round_stat(
        self,
        tick: int,
        day: int,
        day_tick: int,
        phase: str,
        symbol: str,
        buy_volume: int,
        sell_volume: int,
        matched_volume: int,
        open_price: float,
        close_price: float,
    ):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO market_round_stats (
                    tick, day, day_tick, phase, symbol,
                    buy_volume, sell_volume, matched_volume,
                    open_price, close_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tick,
                    day,
                    day_tick,
                    phase,
                    symbol,
                    buy_volume,
                    sell_volume,
                    matched_volume,
                    open_price,
                    close_price,
                ),
            )

    def get_recent_market_round_stats(self, limit: int = 40) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT tick, day, day_tick, phase, symbol,
                       buy_volume, sell_volume, matched_volume,
                       open_price, close_price, timestamp
                FROM market_round_stats
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def get_agent_snapshot(self, agent_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, role, is_active, cash, holdings, trading_memory, social_memory,
                       policy_memory, bulletin_memory, fundamental_memory, episodic_summary, belief_memory, relationship_memory
                FROM agents
                WHERE id = ?
                """,
                (agent_id,),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["is_active"] = bool(result.get("is_active", 1))
            result["holdings"] = _safe_json_loads(result["holdings"], {})
            result["trading_memory"] = _safe_json_loads(result["trading_memory"], [])
            result["social_memory"] = _safe_json_loads(result["social_memory"], [])
            result["policy_memory"] = _safe_json_loads(result["policy_memory"], [])
            result["bulletin_memory"] = _safe_json_loads(result["bulletin_memory"], [])
            result["fundamental_memory"] = _safe_json_loads(result["fundamental_memory"], [])
            result["episodic_summary"] = _safe_json_loads(result["episodic_summary"], {})
            result["belief_memory"] = _safe_json_loads(result["belief_memory"], {})
            result["relationship_memory"] = _safe_json_loads(result["relationship_memory"], {})
            return result

    def set_agent_active(self, agent_id: str, is_active: bool) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE agents
                SET is_active = ?, last_updated = strftime('%s','now')
                WHERE id = ?
                """,
                (1 if is_active else 0, agent_id),
            )
            return cursor.rowcount > 0

    def delete_agent(self, agent_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            # Remove per-agent history too, otherwise orphan rows keep pointing
            # at an agent that no longer exists.
            conn.execute("DELETE FROM asset_history WHERE agent_id = ?", (agent_id,))
            conn.execute("DELETE FROM trade_asset_log WHERE agent_id = ?", (agent_id,))
            return cursor.rowcount > 0

    def get_runtime_state(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT current_tick, day_tick, day_count, phase, base_living_cost,
                       active_policy_type, active_policy_title, active_policy_summary, active_policy_day,
                       active_policy_issued_at, active_policy_expires_at, updated_at
                FROM runtime_state
                WHERE id = 1
                """
            ).fetchone()
            return dict(row) if row else {
                "current_tick": 0,
                "day_tick": 0,
                "day_count": 1,
                "phase": "CLOSED",
                "base_living_cost": 400.0,
                "active_policy_type": None,
                "active_policy_title": None,
                "active_policy_summary": None,
                "active_policy_day": None,
                "active_policy_issued_at": None,
                "active_policy_expires_at": None,
                "updated_at": None,
            }

    def save_runtime_state(
        self,
        current_tick: int,
        day_tick: int,
        day_count: int,
        phase: str,
        base_living_cost: float,
        active_policy_type: str | None = None,
        active_policy_title: str | None = None,
        active_policy_summary: str | None = None,
        active_policy_day: int | None = None,
        active_policy_issued_at: str | None = None,
        active_policy_expires_at: str | None = None,
    ):
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE runtime_state
                SET current_tick = ?, day_tick = ?, day_count = ?, phase = ?, base_living_cost = ?,
                    active_policy_type = ?, active_policy_title = ?, active_policy_summary = ?, active_policy_day = ?,
                    active_policy_issued_at = ?, active_policy_expires_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (
                    current_tick,
                    day_tick,
                    day_count,
                    phase,
                    base_living_cost,
                    active_policy_type,
                    active_policy_title,
                    active_policy_summary,
                    active_policy_day,
                    active_policy_issued_at,
                    active_policy_expires_at,
                ),
            )

    def get_latest_activity_state(self) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT tick, day, day_tick, phase, timestamp
                FROM asset_history
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if row:
                return dict(row)

            row = conn.execute(
                """
                SELECT tick, day, day_tick, phase, timestamp
                FROM market_history
                ORDER BY tick DESC
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row else None

    def save_forum_post(
        self,
        post_id: Optional[int],
        agent_id: str,
        content: str,
        sentiment: float,
        is_hot: bool = False,
        parent_id: Optional[int] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        """Insert a forum post and return the stored id.

        Passing ``post_id=None`` allocates ``MAX(post_id)+1`` under a write lock
        in the same transaction as the insert. An in-memory counter would race
        between the simulation and dashboard processes, and the old
        ``INSERT OR REPLACE`` would silently overwrite the other process's post.
        """
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if post_id is None:
                row = conn.execute("SELECT COALESCE(MAX(post_id), 0) + 1 FROM forum_posts").fetchone()
                post_id = int(row[0])
            conn.execute(
                """
                INSERT INTO forum_posts (
                    post_id, agent_id, content, sentiment, is_hot, parent_id, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    post_id,
                    agent_id,
                    content,
                    sentiment,
                    1 if is_hot else 0,
                    parent_id,
                    timestamp,
                ),
            )
            return int(post_id)

    def get_forum_posts(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        query = """
            SELECT post_id, agent_id, content, sentiment, is_hot, parent_id, timestamp
            FROM forum_posts
            ORDER BY post_id ASC
        """
        params: List[Any] = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_recent_forum_posts(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT post_id, agent_id, content, sentiment, is_hot, parent_id, timestamp
                FROM forum_posts
                ORDER BY post_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def record_policy_event(
        self,
        day: int,
        tick: int,
        day_tick: int,
        policy_type: str,
        title: str,
        summary: str,
        metrics: Dict[str, Any],
        actions: Dict[str, Any],
    ):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO policy_history (
                    day, tick, day_tick, policy_type, title, summary, metrics, actions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day,
                    tick,
                    day_tick,
                    policy_type,
                    title,
                    summary,
                    json.dumps(metrics),
                    json.dumps(actions),
                ),
            )

    def get_recent_policy_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT day, tick, day_tick, policy_type, title, summary, metrics, actions, timestamp
                FROM policy_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            results = []
            for row in reversed(rows):
                item = dict(row)
                item["metrics"] = _safe_json_loads(item["metrics"], {})
                item["actions"] = _safe_json_loads(item["actions"], {})
                results.append(item)
            return results

    def run_health_check(self, prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        prices = prices or self.get_all_stock_prices()
        issues: List[Dict[str, Any]] = []
        negative_cash_agents = 0
        negative_holding_entries = 0
        asset_formula_errors = 0
        checked_agents = 0
        total_cash = 0.0
        total_stock_value = 0.0

        with self._get_connection() as conn:
            agents = conn.execute("SELECT id, cash, holdings FROM agents ORDER BY id").fetchall()
            latest_asset_rows = conn.execute(
                """
                SELECT ah.agent_id, ah.cash, ah.stock_value, ah.total_assets, ah.holdings, ah.tick, ah.source
                FROM asset_history ah
                INNER JOIN (
                    SELECT agent_id, MAX(id) AS latest_id
                    FROM asset_history
                    GROUP BY agent_id
                ) latest ON latest.latest_id = ah.id
                """
            ).fetchall()

        latest_asset_by_agent = {row["agent_id"]: dict(row) for row in latest_asset_rows}

        for row in agents:
            checked_agents += 1
            agent_id = row["id"]
            cash = round(float(row["cash"]), 2)
            holdings = _safe_json_loads(row["holdings"], {})
            stock_value = 0.0
            total_cash += cash

            if cash < 0:
                negative_cash_agents += 1
                issues.append(
                    {
                        "severity": "error",
                        "type": "negative_cash",
                        "agent_id": agent_id,
                        "message": f"{agent_id} has negative cash {cash:.2f}",
                    }
                )

            for symbol, quantity in holdings.items():
                qty = float(quantity)
                if qty < 0:
                    negative_holding_entries += 1
                    issues.append(
                        {
                            "severity": "error",
                            "type": "negative_holding",
                            "agent_id": agent_id,
                            "symbol": symbol,
                            "message": f"{agent_id} has negative holding {symbol}:{qty}",
                        }
                    )
                stock_value += float(prices.get(symbol, 0.0)) * qty

            total_stock_value += stock_value
            latest_asset = latest_asset_by_agent.get(agent_id)
            if latest_asset:
                expected_total = round(float(latest_asset["cash"]) + float(latest_asset["stock_value"]), 2)
                stored_total = round(float(latest_asset["total_assets"]), 2)
                if abs(expected_total - stored_total) > 0.01:
                    asset_formula_errors += 1
                    issues.append(
                        {
                            "severity": "error",
                            "type": "asset_formula_mismatch",
                            "agent_id": agent_id,
                            "tick": latest_asset["tick"],
                            "source": latest_asset["source"],
                            "message": (
                                f"{agent_id} latest asset snapshot mismatch: "
                                f"cash+stock_value={expected_total:.2f}, stored_total={stored_total:.2f}"
                            ),
                        }
                    )

        status = "ok"
        if issues:
            status = "error" if any(item["severity"] == "error" for item in issues) else "warn"

        return {
            "status": status,
            "checked_agents": checked_agents,
            "negative_cash_agents": negative_cash_agents,
            "negative_holding_entries": negative_holding_entries,
            "asset_formula_errors": asset_formula_errors,
            "total_cash": round(total_cash, 2),
            "total_stock_value": round(total_stock_value, 2),
            "total_assets": round(total_cash + total_stock_value, 2),
            "issues": issues,
        }

    def record_daily_audit(
        self,
        day: int,
        tick: int,
        day_tick: int,
        phase: str,
        health_status: str,
        summary: str,
        stats: Dict[str, Any],
        issues: List[Dict[str, Any]],
    ):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO daily_audit (
                    day, tick, day_tick, phase, health_status, summary, stats, issues
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day,
                    tick,
                    day_tick,
                    phase,
                    health_status,
                    summary,
                    json.dumps(stats, ensure_ascii=False),
                    json.dumps(issues, ensure_ascii=False),
                ),
            )

    def get_recent_daily_audits(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT day, tick, day_tick, phase, health_status, summary, stats, issues, timestamp
                FROM daily_audit
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            results = []
            for row in reversed(rows):
                item = dict(row)
                item["stats"] = _safe_json_loads(item["stats"], {})
                item["issues"] = _safe_json_loads(item["issues"], [])
                results.append(item)
            return results

    def repair_account_integrity(self) -> Dict[str, Any]:
        fixed_negative_cash = 0
        fixed_negative_holdings = 0
        with self._get_connection() as conn:
            agents = conn.execute("SELECT id, cash, holdings FROM agents").fetchall()
            for row in agents:
                if float(row["cash"]) < 0:
                    conn.execute("UPDATE agents SET cash = 0 WHERE id = ?", (row["id"],))
                    fixed_negative_cash += 1

                holdings = _safe_json_loads(row["holdings"], {})
                changed = False
                for symbol, quantity in list(holdings.items()):
                    if float(quantity) < 0:
                        holdings[symbol] = 0
                        fixed_negative_holdings += 1
                        changed = True
                if changed:
                    conn.execute(
                        "UPDATE agents SET holdings = ? WHERE id = ?",
                        (json.dumps(holdings, ensure_ascii=False), row["id"]),
                    )

        return {
            "fixed_negative_cash": fixed_negative_cash,
            "fixed_negative_holdings": fixed_negative_holdings,
        }
