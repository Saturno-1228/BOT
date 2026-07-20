from __future__ import annotations

import unittest

from telegram_guardian.telegram_api import TelegramApiError, TelegramClient


class AdministratorCacheTests(unittest.TestCase):
    def test_admin_refreshes_have_a_global_budget(self) -> None:
        now = [100.0]
        client = TelegramClient(
            "123:test",
            admin_refresh_limit_per_minute=2,
            clock=lambda: now[0],
        )
        calls = 0

        def fake_call(method: str, payload: object) -> list[dict[str, object]]:
            nonlocal calls
            calls += 1
            return [{"user": {"id": 7}}]

        client.call = fake_call  # type: ignore[method-assign]
        self.assertTrue(client.is_chat_administrator(-1001, 7))
        self.assertTrue(client.is_chat_administrator(-1002, 7))
        with self.assertRaises(TelegramApiError):
            client.is_chat_administrator(-1003, 7)
        self.assertEqual(calls, 2)

    def test_failed_admin_refresh_is_cached_briefly(self) -> None:
        now = [100.0]
        client = TelegramClient("123:test", clock=lambda: now[0])
        calls = 0

        def fake_call(method: str, payload: object) -> list[dict[str, object]]:
            nonlocal calls
            calls += 1
            raise TelegramApiError("fallo simulado")

        client.call = fake_call  # type: ignore[method-assign]
        with self.assertRaises(TelegramApiError):
            client.is_chat_administrator(-1001, 7)
        self.assertFalse(client.is_chat_administrator(-1001, 7))
        self.assertEqual(calls, 1)

    def test_reuses_admin_set_for_all_users_in_same_chat(self) -> None:
        now = [100.0]
        client = TelegramClient("123:test", clock=lambda: now[0])
        calls: list[tuple[str, object]] = []

        def fake_call(method: str, payload: object) -> list[dict[str, object]]:
            calls.append((method, payload))
            return [{"user": {"id": 7}}, {"user": {"id": 9}}]

        client.call = fake_call  # type: ignore[method-assign]

        self.assertTrue(client.is_chat_administrator(-1001, 7))
        self.assertFalse(client.is_chat_administrator(-1001, 8))
        self.assertEqual(len(calls), 1)

    def test_refreshes_admin_set_after_ttl(self) -> None:
        now = [100.0]
        client = TelegramClient(
            "123:test", admin_cache_ttl_seconds=300, clock=lambda: now[0]
        )
        calls = 0

        def fake_call(method: str, payload: object) -> list[dict[str, object]]:
            nonlocal calls
            calls += 1
            return [{"user": {"id": 7}}]

        client.call = fake_call  # type: ignore[method-assign]
        client.is_chat_administrator(-1001, 7)
        now[0] = 401.0
        client.is_chat_administrator(-1001, 7)

        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
