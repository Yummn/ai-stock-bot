from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class TradingStorage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    cycle INTEGER NOT NULL,
                    account_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    errors_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    run_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    last_price REAL NOT NULL,
                    previous_close REAL NOT NULL,
                    change_pct REAL NOT NULL,
                    sma_5 REAL NOT NULL,
                    sma_20 REAL NOT NULL,
                    volume INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS signals (
                    run_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    run_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    limit_price REAL NOT NULL,
                    reason TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS positions (
                    run_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    average_price REAL NOT NULL,
                    market_price REAL NOT NULL,
                    market_value REAL NOT NULL,
                    pnl_pct REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS histories (
                    run_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL
                );
                """
            )

    def save_latest_state(self, state: dict[str, Any]) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state (key, value_json, updated_at)
                VALUES ('latest', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (json.dumps(state, ensure_ascii=False), now),
            )

    def load_latest_state(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key = 'latest'"
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["value_json"])

    def save_run(self, state: dict[str, Any]) -> int:
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (created_at, cycle, account_json, config_json, errors_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    now,
                    int(state["cycle"]),
                    json.dumps(state["account"], ensure_ascii=False),
                    json.dumps(state["config"], ensure_ascii=False),
                    json.dumps(state.get("errors", []), ensure_ascii=False),
                ),
            )
            run_id = int(cursor.lastrowid)
            self._insert_snapshots(connection, run_id, state.get("snapshots", []))
            self._insert_signals(connection, run_id, state.get("signals", []))
            self._insert_orders(connection, run_id, state.get("orders", []))
            self._insert_positions(connection, run_id, state.get("positions", []))
            self._insert_histories(connection, run_id, state.get("histories", {}))
            connection.execute(
                """
                INSERT INTO app_state (key, value_json, updated_at)
                VALUES ('latest', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (json.dumps(state, ensure_ascii=False), now),
            )
            return run_id

    def _insert_snapshots(self, connection: sqlite3.Connection, run_id: int, snapshots: list[dict[str, Any]]) -> None:
        connection.executemany(
            """
            INSERT INTO snapshots
            (run_id, symbol, last_price, previous_close, change_pct, sma_5, sma_20, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    item["symbol"],
                    item["last_price"],
                    item["previous_close"],
                    item["change_pct"],
                    item["sma_5"],
                    item["sma_20"],
                    item["volume"],
                )
                for item in snapshots
            ],
        )

    def _insert_signals(self, connection: sqlite3.Connection, run_id: int, signals: list[dict[str, Any]]) -> None:
        connection.executemany(
            """
            INSERT INTO signals (run_id, symbol, side, confidence, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (run_id, item["symbol"], item["side"], item["confidence"], item["reason"])
                for item in signals
            ],
        )

    def _insert_orders(self, connection: sqlite3.Connection, run_id: int, orders: list[dict[str, Any]]) -> None:
        connection.executemany(
            """
            INSERT INTO orders (run_id, symbol, side, quantity, limit_price, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (run_id, item["symbol"], item["side"], item["quantity"], item["limit_price"], item["reason"])
                for item in orders
            ],
        )

    def _insert_positions(self, connection: sqlite3.Connection, run_id: int, positions: list[dict[str, Any]]) -> None:
        connection.executemany(
            """
            INSERT INTO positions
            (run_id, symbol, quantity, average_price, market_price, market_value, pnl_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    item["symbol"],
                    item["quantity"],
                    item["average_price"],
                    item["market_price"],
                    item["market_value"],
                    item["pnl_pct"],
                )
                for item in positions
            ],
        )

    def _insert_histories(self, connection: sqlite3.Connection, run_id: int, histories: dict[str, list[dict[str, Any]]]) -> None:
        rows: list[tuple[Any, ...]] = []
        for symbol, bars in histories.items():
            for item in bars:
                rows.append((run_id, symbol, item["timestamp"], item["close"], item["volume"]))
        connection.executemany(
            """
            INSERT INTO histories (run_id, symbol, timestamp, close, volume)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
