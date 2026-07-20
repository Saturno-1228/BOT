from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from telegram_guardian.storage import Storage


class StorageLimitTests(unittest.TestCase):
    def test_events_are_idempotent_per_update_and_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.db"
            storage = Storage(database_path)
            storage.initialize()

            for _ in range(3):
                storage.record_event(
                    update_id=10,
                    event_type="command:estado",
                    chat_id=-1,
                    user_id=7,
                )

            with closing(sqlite3.connect(database_path)) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM events"
                ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_initialize_deduplicates_legacy_events_before_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.db"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        update_id INTEGER,
                        event_type TEXT NOT NULL,
                        chat_id INTEGER,
                        user_id INTEGER,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO events(update_id, event_type)
                    VALUES (10, 'command:estado'), (10, 'command:estado');
                    """
                )

            Storage(database_path).initialize()

            with closing(sqlite3.connect(database_path)) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM events"
                ).fetchone()[0]
                index_exists = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'index'
                      AND name = 'idx_events_update_type'
                    """
                ).fetchone()[0]
            self.assertEqual(count, 1)
            self.assertEqual(index_exists, 1)

    def test_row_caps_bound_events_and_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.db"
            storage = Storage(
                database_path,
                max_event_rows=100,
                max_signal_rows=100,
                compaction_every_writes=1,
            )
            storage.initialize()

            for index in range(250):
                storage.record_event(
                    update_id=index,
                    event_type="command:test",
                    chat_id=-1,
                    user_id=index,
                )
                storage.record_signal(
                    update_id=index,
                    chat_id=-1,
                    user_id=index,
                    detector="test",
                    score=1,
                    severity="baja",
                    reason="conteo sintetico",
                )

            with closing(sqlite3.connect(database_path)) as connection:
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM events"
                ).fetchone()[0]
                signal_count = connection.execute(
                    "SELECT COUNT(*) FROM moderation_signals"
                ).fetchone()[0]
            self.assertLessEqual(event_count, 100)
            self.assertLessEqual(signal_count, 100)

    def test_purge_removes_old_events_and_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.db"
            storage = Storage(database_path)
            storage.initialize()
            storage.record_event(update_id=1, event_type="command:test")
            storage.record_signal(
                update_id=1,
                chat_id=-1,
                user_id=1,
                detector="test",
                score=1,
                severity="baja",
                reason="conteo sintetico",
            )
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "UPDATE events SET created_at = '2000-01-01 00:00:00'"
                )
                connection.execute(
                    "UPDATE moderation_signals SET created_at = '2000-01-01 00:00:00'"
                )
                connection.commit()

            storage.purge_old_records(7)

            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM moderation_signals"
                    ).fetchone()[0],
                    0,
                )

    def test_initialize_caps_an_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.db"
            storage = Storage(
                database_path,
                max_event_rows=100,
                compaction_every_writes=10_000,
            )
            storage.initialize()
            for index in range(150):
                storage.record_event(
                    update_id=index,
                    event_type="command:test",
                )

            reopened = Storage(database_path, max_event_rows=100)
            reopened.initialize()

            with closing(sqlite3.connect(database_path)) as connection:
                count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(count, 100)


if __name__ == "__main__":
    unittest.main()
