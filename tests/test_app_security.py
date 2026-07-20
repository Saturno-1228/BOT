from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from telegram_guardian.app import (
    GLOBAL_RESPONSE_LIMIT_PER_MINUTE,
    GLOBAL_RESPONSE_LIMIT_PER_SECOND,
    MAINTENANCE_INTERVAL_SECONDS,
    SalomeApp,
)
from telegram_guardian.config import Settings
from telegram_guardian.moderation import ModerationPolicy, ObservationEngine
from telegram_guardian.telegram_api import TelegramApiError


def policy() -> ModerationPolicy:
    return ModerationPolicy(
        mode="observe",
        signal_cooldown_seconds=30,
        signal_retention_days=7,
        flood_max_messages=6,
        flood_window_seconds=10,
        duplicate_max_repetitions=3,
        duplicate_window_seconds=60,
        duplicate_minimum_length=8,
        link_max_messages=3,
        link_window_seconds=60,
        mention_max_per_message=5,
    )


class FakeStorage:
    def __init__(self) -> None:
        self.purge_calls: list[int] = []
        self.events: list[dict[str, object]] = []
        self.offsets: list[int] = []

    def purge_old_records(self, retention_days: int) -> None:
        self.purge_calls.append(retention_days)

    def record_signal(self, **values: object) -> None:
        raise AssertionError(f"No se esperaba una señal: {values}")

    def recent_signals(self, chat_id: int) -> list[dict[str, object]]:
        return []

    def record_event(self, **values: object) -> None:
        self.events.append(values)

    def set_offset(self, offset: int) -> None:
        self.offsets.append(offset)


class FakeClient:
    def __init__(
        self,
        is_admin: bool = False,
        failing_chats: set[int] | None = None,
    ) -> None:
        self.is_admin = is_admin
        self.failing_chats = failing_chats or set()
        self.admin_checks = 0
        self.sent: list[tuple[int, str]] = []

    def is_chat_administrator(self, chat_id: int, user_id: int) -> bool:
        self.admin_checks += 1
        return self.is_admin

    def send_message(self, chat_id: int, text: str) -> dict[str, object]:
        if chat_id in self.failing_chats:
            raise TelegramApiError("fallo simulado de envio")
        self.sent.append((chat_id, text))
        return {}


class AppSecurityTests(unittest.TestCase):
    def build_app(
        self,
        now: list[float],
        client: FakeClient,
        storage: FakeStorage,
        **app_options: object,
    ) -> SalomeApp:
        root = Path(tempfile.gettempdir())
        settings = Settings(
            root=root,
            telegram_bot_token="123:test",
            telegram_bot_username="Salome_G_BOT",
            database_path=root / "salome-test.db",
            log_level="INFO",
        )
        app_options.setdefault("wall_clock", lambda: now[0])
        return SalomeApp(
            settings,
            client,  # type: ignore[arg-type]
            storage,  # type: ignore[arg-type]
            ObservationEngine(policy(), wall_clock=lambda: now[0]),
            logging.getLogger("salome-test"),
            clock=lambda: now[0],
            **app_options,
        )

    def test_daily_maintenance_runs_only_when_due(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        app = self.build_app(now, FakeClient(), storage)

        self.assertFalse(app.run_maintenance_if_due())
        now[0] += MAINTENANCE_INTERVAL_SECONDS
        self.assertTrue(app.run_maintenance_if_due())
        self.assertFalse(app.run_maintenance_if_due())
        self.assertEqual(storage.purge_calls, [7])

    def test_unauthorized_risk_report_is_silent(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=False)
        app = self.build_app(now, client, storage)

        app.handle_update(
            {
                "update_id": 10,
                "message": {
                    "text": "/riesgos",
                    "date": 100,
                    "chat": {"id": -1001, "type": "supergroup"},
                    "from": {"id": 8},
                },
            }
        )
        app.handle_update(
            {
                "update_id": 11,
                "message": {
                    "text": "/riesgos",
                    "date": 101,
                    "chat": {"id": -1001, "type": "supergroup"},
                    "from": {"id": 8},
                },
            }
        )

        self.assertEqual(client.admin_checks, 1)
        self.assertEqual(client.sent, [])
        self.assertEqual(storage.events, [])

    def test_risk_report_never_checks_admins_in_private_chat(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(now, client, storage)

        app.handle_update(
            {
                "update_id": 12,
                "message": {
                    "text": "/riesgos",
                    "date": 100,
                    "chat": {"id": 8, "type": "private"},
                    "from": {"id": 8},
                },
            }
        )

        self.assertEqual(client.admin_checks, 0)
        self.assertEqual(client.sent, [])

    def test_network_delay_does_not_bypass_command_cooldown(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(now, client, storage)

        first = {
            "update_id": 20,
            "message": {
                "text": "/estado",
                "date": 100,
                "chat": {"id": -1001},
                "from": {"id": 7},
            },
        }
        second = {
            "update_id": 21,
            "message": {
                "text": "/estado",
                "date": 101,
                "chat": {"id": -1001},
                "from": {"id": 7},
            },
        }

        app.handle_update(first)
        now[0] = 125.0  # Simula una llamada HTTP que bloqueo durante 25 segundos.
        app.handle_update(second)

        self.assertEqual(len(client.sent), 1)
        self.assertEqual(len(storage.events), 1)

    def test_future_timestamp_in_another_chat_does_not_freeze_commands(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(now, client, storage)

        for update_id, chat_id, user_id, message_time in (
            (22, -2001, 8, 10_000),
            (23, -2002, 9, 100),
        ):
            now[0] += 2
            app.handle_update(
                {
                    "update_id": update_id,
                    "message": {
                        "text": "/estado",
                        "date": message_time,
                        "chat": {"id": chat_id},
                        "from": {"id": user_id},
                    },
                }
            )

        now[0] += 4
        app.handle_update(
            {
                "update_id": 24,
                "message": {
                    "text": "/estado",
                    "date": 104,
                    "chat": {"id": -2002},
                    "from": {"id": 9},
                },
            }
        )

        self.assertEqual(
            [chat_id for chat_id, _ in client.sent], [-2001, -2002, -2002]
        )
        self.assertEqual(len(storage.events), 3)

    def test_same_user_is_limited_across_different_chats(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(now, client, storage)

        for update_id, chat_id, message_time in (
            (50, -7001, 100),
            (51, -7002, 101),
        ):
            app.handle_update(
                {
                    "update_id": update_id,
                    "message": {
                        "text": "/estado",
                        "date": message_time,
                        "chat": {"id": chat_id},
                        "from": {"id": 30},
                    },
                }
            )
            now[0] += 1

        self.assertEqual([chat_id for chat_id, _ in client.sent], [-7001])

    def test_compaction_branch_cannot_erase_another_users_cooldown(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(now, client, storage)

        app.handle_update(
            {
                "update_id": 25,
                "message": {
                    "text": "/estado",
                    "date": 100,
                    "chat": {"id": -3001},
                    "from": {"id": 10},
                },
            }
        )
        app._command_checks = 255
        now[0] = 101
        app.handle_update(
            {
                "update_id": 26,
                "message": {
                    "text": "/estado",
                    "date": 100_000,
                    "chat": {"id": -3002},
                    "from": {"id": 11},
                },
            }
        )
        now[0] = 102
        app.handle_update(
            {
                "update_id": 27,
                "message": {
                    "text": "/estado",
                    "date": 101,
                    "chat": {"id": -3001},
                    "from": {"id": 10},
                },
            }
        )

        self.assertEqual([chat_id for chat_id, _ in client.sent], [-3001, -3002])
        self.assertEqual(len(storage.events), 2)

    def test_missing_and_invalid_dates_use_the_wall_clock(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(now, client, storage)

        app.handle_update(
            {
                "update_id": 28,
                "message": {
                    "text": "/estado",
                    "chat": {"id": -4001},
                    "from": {"id": 12},
                },
            }
        )
        now[0] = 101
        app.handle_update(
            {
                "update_id": 29,
                "message": {
                    "text": "/estado",
                    "date": float("nan"),
                    "chat": {"id": -4001},
                    "from": {"id": 12},
                },
            }
        )

        self.assertEqual(len(client.sent), 1)
        self.assertEqual(app._safe_message_time(100_000), 101)

    def test_state_maintenance_expires_memory_without_new_messages(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        app = self.build_app(now, FakeClient(), storage)
        app.moderation.analyze(
            {
                "text": "mensaje suficientemente largo",
                "date": 100,
                "chat": {"id": -5001},
                "from": {"id": 13},
            }
        )
        app._command_input_allowed(-5001, 13, 100)

        now[0] = 200
        self.assertTrue(app.run_state_maintenance_if_due())
        self.assertEqual(
            app.moderation.state_sizes(),
            {
                "message_windows": 0,
                "link_windows": 0,
                "duplicate_fingerprints": 0,
                "signal_cooldowns": 0,
            },
        )
        self.assertEqual(app.runtime_state_sizes()["command_users"], 0)
        self.assertEqual(
            app.runtime_state_sizes()["global_command_users"], 0
        )

    def test_queued_commands_cannot_burst_responses(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(now, client, storage)

        app.handle_update(
            {
                "update_id": 30,
                "message": {
                    "text": "/estado",
                    "date": 100,
                    "chat": {"id": -1001},
                    "from": {"id": 7},
                },
            }
        )
        now[0] = 100.5
        app.handle_update(
            {
                "update_id": 31,
                "message": {
                    "text": "/estado",
                    "date": 110,
                    "chat": {"id": -1001},
                    "from": {"id": 7},
                },
            }
        )

        self.assertEqual(len(client.sent), 1)
        self.assertEqual(len(storage.events), 1)

    def test_failed_send_is_skipped_and_next_update_is_processed(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True, failing_chats={-6001})
        app = self.build_app(now, client, storage)
        updates = [
            {
                "update_id": 40,
                "message": {
                    "text": "/estado",
                    "date": 100,
                    "chat": {"id": -6001, "type": "supergroup"},
                    "from": {"id": 20},
                },
            },
            {
                "update_id": 41,
                "message": {
                    "text": "/estado",
                    "date": 100,
                    "chat": {"id": -6002, "type": "supergroup"},
                    "from": {"id": 21},
                },
            },
        ]

        offset = app.process_updates(updates, None)

        self.assertEqual(offset, 42)
        self.assertEqual(storage.offsets, [41, 42])
        self.assertEqual([chat_id for chat_id, _ in client.sent], [-6002])
        self.assertEqual(len(storage.events), 1)

    def test_global_response_budget_is_bounded(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(now, client, storage)

        for index in range(GLOBAL_RESPONSE_LIMIT_PER_MINUTE + 25):
            now[0] += 0.11
            app.handle_update(
                {
                    "update_id": 10_000 + index,
                    "message": {
                        "text": "/estado",
                        "date": 100,
                        "chat": {"id": -20_000 - index},
                        "from": {"id": 30_000 + index},
                    },
                }
            )

        self.assertEqual(len(client.sent), GLOBAL_RESPONSE_LIMIT_PER_MINUTE)
        self.assertEqual(len(storage.events), GLOBAL_RESPONSE_LIMIT_PER_MINUTE)
        self.assertEqual(
            app.runtime_state_sizes()["global_responses"],
            GLOBAL_RESPONSE_LIMIT_PER_MINUTE,
        )

    def test_global_response_burst_is_bounded(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(now, client, storage)

        for index in range(GLOBAL_RESPONSE_LIMIT_PER_SECOND + 5):
            app.handle_update(
                {
                    "update_id": 30_000 + index,
                    "message": {
                        "text": "/estado",
                        "date": 100,
                        "chat": {"id": -40_000 - index},
                        "from": {"id": 50_000 + index},
                    },
                }
            )

        self.assertEqual(len(client.sent), GLOBAL_RESPONSE_LIMIT_PER_SECOND)

    def test_command_state_is_bounded_under_many_users_and_chats(self) -> None:
        now = [100.0]
        storage = FakeStorage()
        client = FakeClient(is_admin=True)
        app = self.build_app(
            now,
            client,
            storage,
            command_user_max_entries=100,
            command_chat_max_entries=100,
        )

        for index in range(500):
            now[0] += 1.1
            app.handle_update(
                {
                    "update_id": 1000 + index,
                    "message": {
                        "text": "/estado",
                        "date": 1000 + index,
                        "chat": {"id": -10_000 - index},
                        "from": {"id": 20_000 + index},
                    },
                }
            )

        sizes = app.runtime_state_sizes()
        self.assertLessEqual(sizes["command_users"], 100)
        self.assertLessEqual(sizes["global_command_users"], 100)
        self.assertLessEqual(sizes["command_chats"], 100)
        self.assertLessEqual(sizes["response_windows"], 100)


if __name__ == "__main__":
    unittest.main()
