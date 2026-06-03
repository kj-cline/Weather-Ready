from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import duckdb

from stormready_v3.config.settings import DEFAULT_DB_PATH, MIGRATIONS_ROOT


class Database:
    _CONNECT_LOCK = threading.Lock()
    _INITIALIZED_LOCK = threading.Lock()
    _INITIALIZED_PATHS: set[str] = set()

    def __init__(self, db_path: Path | None = None, migrations_root: Path | None = None) -> None:
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.migrations_root = Path(migrations_root or MIGRATIONS_ROOT)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: duckdb.DuckDBPyConnection | None = None

    def _db_key(self) -> str:
        return str(self.db_path.resolve())

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            with self._CONNECT_LOCK:
                last_error: Exception | None = None
                for attempt in range(10):
                    try:
                        self._conn = duckdb.connect(str(self.db_path))
                        break
                    except (duckdb.BinderException, duckdb.IOException) as exc:
                        last_error = exc
                        message = str(exc)
                        transient_lock = (
                            "Unique file handle conflict" in message
                            or "Conflicting lock is held" in message
                        )
                        if not transient_lock or attempt == 9:
                            raise
                        time.sleep(0.05 * (attempt + 1))
                if self._conn is None and last_error is not None:
                    raise last_error
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> duckdb.DuckDBPyConnection:
        conn = self.connect()
        if params is None:
            return conn.execute(sql)
        return conn.execute(sql, params)

    def fetchall(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> list[tuple[Any, ...]]:
        return self.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> tuple[Any, ...] | None:
        return self.execute(sql, params).fetchone()

    def initialize(self) -> None:
        db_key = self._db_key()
        with self._INITIALIZED_LOCK:
            if db_key in self._INITIALIZED_PATHS:
                return
            self.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for migration_file in sorted(self.migrations_root.glob("*.sql")):
                version = migration_file.stem
                already_applied = self.fetchone(
                    "SELECT version FROM schema_migrations WHERE version = ?",
                    [version],
                )
                if already_applied:
                    continue
                sql = migration_file.read_text(encoding="utf-8")
                self.execute(sql)
                self.execute("INSERT INTO schema_migrations(version) VALUES (?)", [version])
            self._INITIALIZED_PATHS.add(db_key)

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
