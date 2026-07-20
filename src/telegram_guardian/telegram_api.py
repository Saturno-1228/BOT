"""Cliente pequeno para la API oficial de bots de Telegram."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable
from typing import Any


class TelegramApiError(RuntimeError):
    """Error sanitizado que nunca incluye el token del bot."""


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        admin_cache_ttl_seconds: int = 300,
        admin_refresh_limit_per_minute: int = 6,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}/"
        self._admin_cache_ttl_seconds = max(30, admin_cache_ttl_seconds)
        self._admin_refresh_limit_per_minute = max(
            1, min(admin_refresh_limit_per_minute, 60)
        )
        self._clock = clock
        self._admin_cache: dict[int, tuple[float, frozenset[int]]] = {}
        self._admin_refresh_times: deque[float] = deque(
            maxlen=self._admin_refresh_limit_per_minute
        )

    def _store_admin_cache(
        self, chat_id: int, cached: tuple[float, frozenset[int]]
    ) -> None:
        if len(self._admin_cache) >= 1024 and chat_id not in self._admin_cache:
            oldest_chat = min(
                self._admin_cache,
                key=lambda key: self._admin_cache[key][0],
            )
            self._admin_cache.pop(oldest_chat, None)
        self._admin_cache[chat_id] = cached

    def call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int = 20,
    ) -> Any:
        encoded_payload: dict[str, str] = {}
        for key, value in (payload or {}).items():
            encoded_payload[key] = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (list, dict))
                else str(value)
            )
        data = urllib.parse.urlencode(encoded_payload).encode("utf-8")
        request = urllib.request.Request(
            self._base_url + method,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TelegramApiError(
                f"Telegram rechazo la solicitud (HTTP {exc.code})"
            ) from None
        except urllib.error.URLError:
            raise TelegramApiError("no fue posible conectar con Telegram") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise TelegramApiError("Telegram devolvio una respuesta no valida") from None

        if not result.get("ok"):
            error_code = result.get("error_code", "desconocido")
            description = result.get("description", "solicitud rechazada")
            raise TelegramApiError(f"Telegram {error_code}: {description}")
        return result.get("result")

    def get_me(self) -> dict[str, Any]:
        return self.call("getMe")

    def get_updates(
        self, offset: int | None, *, timeout: int = 30
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload, timeout=timeout + 10)

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        return self.call(
            "sendMessage",
            {"chat_id": chat_id, "text": text},
        )

    def is_chat_administrator(self, chat_id: int, user_id: int) -> bool:
        now = self._clock()
        cached = self._admin_cache.get(chat_id)
        if cached is None or now >= cached[0]:
            boundary = now - 60
            while (
                self._admin_refresh_times
                and self._admin_refresh_times[0] < boundary
            ):
                self._admin_refresh_times.popleft()
            if (
                len(self._admin_refresh_times)
                >= self._admin_refresh_limit_per_minute
            ):
                raise TelegramApiError(
                    "limite temporal de comprobaciones administrativas"
                )
            self._admin_refresh_times.append(now)
            try:
                administrators = self.call(
                    "getChatAdministrators", {"chat_id": chat_id}
                )
            except TelegramApiError:
                # Una falla transitoria se memoriza brevemente para que el
                # mismo chat no provoque solicitudes nuevas en cada comando.
                self._store_admin_cache(
                    chat_id, (now + 30, frozenset())
                )
                raise
            admin_ids = frozenset(
                int(item.get("user", {}).get("id", 0))
                for item in administrators
                if item.get("user", {}).get("id") is not None
            )
            cached = (now + self._admin_cache_ttl_seconds, admin_ids)
            self._store_admin_cache(chat_id, cached)
        return user_id in cached[1]
