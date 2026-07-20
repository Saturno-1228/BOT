"""Almacenamiento local minimo para eventos y continuidad del polling."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_MAX_EVENT_ROWS = 50_000
DEFAULT_MAX_SIGNAL_ROWS = 50_000
DEFAULT_COMPACTION_EVERY_WRITES = 256


class Storage:
    def __init__(
        self,
        database_path: Path,
        *,
        max_event_rows: int = DEFAULT_MAX_EVENT_ROWS,
        max_signal_rows: int = DEFAULT_MAX_SIGNAL_ROWS,
        compaction_every_writes: int = DEFAULT_COMPACTION_EVERY_WRITES,
    ) -> None:
        self.database_path = database_path
        self.max_event_rows = max(100, max_event_rows)
        self.max_signal_rows = max(100, max_signal_rows)
        self.compaction_every_writes = max(1, compaction_every_writes)
        self._writes_since_compaction = 0

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_id INTEGER,
                    event_type TEXT NOT NULL,
                    chat_id INTEGER,
                    user_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS moderation_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_id INTEGER,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    detector TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    severity TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_signals_chat_created
                ON moderation_signals(chat_id, created_at DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_update_detector
                ON moderation_signals(update_id, detector);

                CREATE INDEX IF NOT EXISTS idx_events_created
                ON events(created_at);

                CREATE INDEX IF NOT EXISTS idx_signals_created
                ON moderation_signals(created_at);
                """
            )
            self._enforce_table_limit(
                connection, "events", self.max_event_rows
            )
            self._enforce_table_limit(
                connection, "moderation_signals", self.max_signal_rows
            )
            connection.execute(
                """
                DELETE FROM events
                WHERE update_id IS NOT NULL
                  AND id NOT IN (
                      SELECT MIN(id)
                      FROM events
                      WHERE update_id IS NOT NULL
                      GROUP BY update_id, event_type
                  )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_events_update_type
                ON events(update_id, event_type)
                WHERE update_id IS NOT NULL
                """
            )
            self._writes_since_compaction = 0

    @staticmethod
    def _enforce_table_limit(
        connection: sqlite3.Connection, table: str, max_rows: int
    ) -> None:
        if table not in {"events", "moderation_signals"}:
            raise ValueError("tabla no permitida")
        connection.execute(
            f"""
            DELETE FROM {table}
            WHERE id <= COALESCE(
                (
                    SELECT id FROM {table}
                    ORDER BY id DESC
                    LIMIT 1 OFFSET ?
                ),
                -1
            )
            """,
            (max_rows,),
        )

    def _compact_if_due(self, connection: sqlite3.Connection) -> None:
        self._writes_since_compaction += 1
        if self._writes_since_compaction < self.compaction_every_writes:
            return
        self._enforce_table_limit(connection, "events", self.max_event_rows)
        self._enforce_table_limit(
            connection, "moderation_signals", self.max_signal_rows
        )
        self._writes_since_compaction = 0

    def get_offset(self) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'telegram_offset'"
            ).fetchone()
        return int(row[0]) if row else None

    def set_offset(self, offset: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO metadata(key, value) VALUES('telegram_offset', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(offset),),
            )

    def record_event(
        self,
        *,
        update_id: int | None,
        event_type: str,
        chat_id: int | None = None,
        user_id: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO events(
                    update_id, event_type, chat_id, user_id
                )
                VALUES (?, ?, ?, ?)
                """,
                (update_id, event_type, chat_id, user_id),
            )
            self._compact_if_due(connection)

    def record_signal(
        self,
        *,
        update_id: int,
        chat_id: int,
        user_id: int,
        detector: str,
        score: int,
        severity: str,
        reason: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO moderation_signals(
                    update_id, chat_id, user_id, detector, score, severity, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (update_id, chat_id, user_id, detector, score, severity, reason),
            )
            self._compact_if_due(connection)

    def purge_old_records(self, retention_days: int) -> None:
        safe_days = max(1, min(retention_days, 365))
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM moderation_signals
                WHERE created_at < datetime('now', ?)
                """,
                (f"-{safe_days} days",),
            )
            connection.execute(
                """
                DELETE FROM events
                WHERE created_at < datetime('now', ?)
                """,
                (f"-{safe_days} days",),
            )
            self._enforce_table_limit(
                connection, "events", self.max_event_rows
            )
            self._enforce_table_limit(
                connection, "moderation_signals", self.max_signal_rows
            )
            self._writes_since_compaction = 0

        # Trunca el WAL despues del commit. El archivo principal conserva su
        # marca de agua, pero los limites de filas impiden crecimiento continuo.
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def recent_signals(self, chat_id: int, limit: int = 5) -> list[dict[str, object]]:
        safe_limit = max(1, min(limit, 20))
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT detector, score, severity, reason, user_id, created_at
                FROM moderation_signals
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (chat_id, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]
