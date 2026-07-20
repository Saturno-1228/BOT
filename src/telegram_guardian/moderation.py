"""Detectores deterministas de riesgo que no conservan el texto analizado."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from bisect import bisect_left, bisect_right, insort
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigurationError


URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.|t\.me/)\S+")
MENTION_PATTERN = re.compile(r"(?<!\w)@[A-Za-z0-9_]{3,}")
WHITESPACE_PATTERN = re.compile(r"\s+")
DEFAULT_MAX_USER_WINDOWS = 10_000
DEFAULT_MAX_DUPLICATE_FINGERPRINTS = 20_000
DEFAULT_MAX_SIGNAL_COOLDOWNS = 10_000
DEFAULT_CLEANUP_INTERVAL_MESSAGES = 256


@dataclass(frozen=True)
class ModerationPolicy:
    mode: str
    signal_cooldown_seconds: int
    signal_retention_days: int
    flood_max_messages: int
    flood_window_seconds: int
    duplicate_max_repetitions: int
    duplicate_window_seconds: int
    duplicate_minimum_length: int
    link_max_messages: int
    link_window_seconds: int
    mention_max_per_message: int

    @classmethod
    def load(cls, path: Path) -> "ModerationPolicy":
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            moderation = document["moderation"]
            policy = cls(
                mode=str(moderation["mode"]),
                signal_cooldown_seconds=int(
                    moderation["signal_cooldown_seconds"]
                ),
                signal_retention_days=int(moderation["signal_retention_days"]),
                flood_max_messages=int(moderation["flood"]["max_messages"]),
                flood_window_seconds=int(moderation["flood"]["window_seconds"]),
                duplicate_max_repetitions=int(
                    moderation["duplicates"]["max_repetitions"]
                ),
                duplicate_window_seconds=int(
                    moderation["duplicates"]["window_seconds"]
                ),
                duplicate_minimum_length=int(
                    moderation["duplicates"]["minimum_length"]
                ),
                link_max_messages=int(
                    moderation["links"]["max_messages_with_links"]
                ),
                link_window_seconds=int(moderation["links"]["window_seconds"]),
                mention_max_per_message=int(
                    moderation["mentions"]["max_mentions_per_message"]
                ),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ConfigurationError(
                "config/settings.json no contiene una politica valida"
            ) from None

        numeric_values = (
            policy.signal_cooldown_seconds,
            policy.signal_retention_days,
            policy.flood_max_messages,
            policy.flood_window_seconds,
            policy.duplicate_max_repetitions,
            policy.duplicate_window_seconds,
            policy.duplicate_minimum_length,
            policy.link_max_messages,
            policy.link_window_seconds,
            policy.mention_max_per_message,
        )
        if policy.mode != "observe" or any(value <= 0 for value in numeric_values):
            raise ConfigurationError(
                "la primera version exige modo observe y umbrales positivos"
            )
        return policy


@dataclass(frozen=True)
class Signal:
    detector: str
    score: int
    severity: str
    reason: str


@dataclass(frozen=True)
class ModerationResult:
    signals: tuple[Signal, ...]
    total_score: int
    severity: str


def severity_for(score: int) -> str:
    if score >= 70:
        return "alta"
    if score >= 40:
        return "media"
    return "baja"


class ObservationEngine:
    def __init__(
        self,
        policy: ModerationPolicy,
        *,
        max_user_windows: int = DEFAULT_MAX_USER_WINDOWS,
        max_duplicate_fingerprints: int = DEFAULT_MAX_DUPLICATE_FINGERPRINTS,
        max_signal_cooldowns: int = DEFAULT_MAX_SIGNAL_COOLDOWNS,
        cleanup_interval_messages: int = DEFAULT_CLEANUP_INTERVAL_MESSAGES,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.policy = policy
        self.max_user_windows = max(100, max_user_windows)
        self.max_duplicate_fingerprints = max(100, max_duplicate_fingerprints)
        self.max_signal_cooldowns = max(100, max_signal_cooldowns)
        self.cleanup_interval_messages = max(1, cleanup_interval_messages)
        self._wall_clock = wall_clock
        self._messages_analyzed = 0
        self._message_times: OrderedDict[
            tuple[int, int], list[int]
        ] = OrderedDict()
        self._link_times: OrderedDict[
            tuple[int, int], list[int]
        ] = OrderedDict()
        self._duplicate_times: OrderedDict[
            tuple[int, int, str], list[int]
        ] = OrderedDict()
        self._last_signal: OrderedDict[tuple[int, int, str], int] = OrderedDict()

    @staticmethod
    def _bounded_window(
        mapping: OrderedDict[Any, list[int]],
        key: Any,
        *,
        max_entries: int,
    ) -> list[int]:
        values = mapping.get(key)
        if values is not None:
            mapping.move_to_end(key)
            return values
        if len(mapping) >= max_entries:
            mapping.popitem(last=False)
        values = []
        mapping[key] = values
        return values

    @staticmethod
    def _insert_bounded(values: list[int], timestamp: int, maxlen: int) -> None:
        insort(values, timestamp)
        overflow = len(values) - max(1, maxlen)
        if overflow > 0:
            del values[:overflow]

    @staticmethod
    def _prune(values: list[int], now: int, window: int) -> None:
        boundary = now - window
        expired = bisect_left(values, boundary)
        if expired:
            del values[:expired]

    @staticmethod
    def _window_count(values: list[int], now: int, window: int) -> int:
        return bisect_right(values, now) - bisect_left(values, now - window)

    @classmethod
    def _compact_window_map(
        cls, mapping: OrderedDict[Any, list[int]], now: int, window: int
    ) -> None:
        for key in list(mapping):
            values = mapping[key]
            cls._prune(values, now, window)
            if not values:
                del mapping[key]

    def _compact_state_if_due(self) -> None:
        self._messages_analyzed += 1
        if self._messages_analyzed < self.cleanup_interval_messages:
            return
        self._messages_analyzed = 0
        self.compact()

    def compact(self, epoch_now: int | None = None) -> None:
        """Elimina estado vencido aunque no lleguen mensajes nuevos."""

        now = int(self._wall_clock()) if epoch_now is None else int(epoch_now)
        self._compact_window_map(
            self._message_times, now, self.policy.flood_window_seconds
        )
        self._compact_window_map(
            self._link_times, now, self.policy.link_window_seconds
        )
        self._compact_window_map(
            self._duplicate_times, now, self.policy.duplicate_window_seconds
        )
        signal_boundary = now - self.policy.signal_cooldown_seconds
        for key in list(self._last_signal):
            if self._last_signal[key] < signal_boundary:
                del self._last_signal[key]

    def state_sizes(self) -> dict[str, int]:
        return {
            "message_windows": len(self._message_times),
            "link_windows": len(self._link_times),
            "duplicate_fingerprints": len(self._duplicate_times),
            "signal_cooldowns": len(self._last_signal),
        }

    def _event_timestamp(self, message: dict[str, Any]) -> int:
        wall_now = int(self._wall_clock())
        value = message.get("date")
        if isinstance(value, bool):
            return wall_now
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return wall_now
        if not math.isfinite(parsed):
            return wall_now
        # Un reloj futuro no debe vaciar ventanas de otros chats ni congelar
        # cooldowns. Las fechas antiguas se conservan para ordenar el backlog.
        return min(int(parsed), wall_now)

    @staticmethod
    def _content(message: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        if isinstance(message.get("text"), str):
            return str(message["text"])[:8192], list(message.get("entities") or [])
        if isinstance(message.get("caption"), str):
            return (
                str(message["caption"])[:8192],
                list(message.get("caption_entities") or []),
            )
        return "", []

    @staticmethod
    def _entity_text(text: str, entity: dict[str, Any]) -> str:
        try:
            start = int(entity["offset"]) * 2
            end = start + int(entity["length"]) * 2
            return text.encode("utf-16-le")[start:end].decode("utf-16-le")
        except (KeyError, TypeError, ValueError, UnicodeError):
            return ""

    @classmethod
    def _has_link(cls, text: str, entities: list[dict[str, Any]]) -> bool:
        if any(entity.get("type") in {"url", "text_link"} for entity in entities):
            return True
        return bool(URL_PATTERN.search(text))

    @classmethod
    def _unique_mentions(
        cls, text: str, entities: list[dict[str, Any]]
    ) -> set[str]:
        mentions: set[str] = set()
        for entity in entities:
            entity_type = entity.get("type")
            if entity_type == "mention":
                value = cls._entity_text(text, entity).casefold()
                if value:
                    mentions.add(value)
            elif entity_type == "text_mention":
                user_id = entity.get("user", {}).get("id")
                if user_id is not None:
                    mentions.add(f"id:{user_id}")
        if not mentions:
            mentions.update(value.casefold() for value in MENTION_PATTERN.findall(text))
        return mentions

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text.casefold())
        normalized = "".join(
            character
            for character in normalized
            if unicodedata.category(character) != "Cf"
        )
        return WHITESPACE_PATTERN.sub(" ", normalized).strip()

    def _emit_once(
        self,
        *,
        chat_id: int,
        user_id: int,
        detector: str,
        now: int,
        score: int,
        reason: str,
    ) -> Signal | None:
        key = (chat_id, user_id, detector)
        previous = self._last_signal.get(key)
        if (
            previous is not None
            and now - previous < self.policy.signal_cooldown_seconds
        ):
            self._last_signal.move_to_end(key)
            return None
        if (
            key not in self._last_signal
            and len(self._last_signal) >= self.max_signal_cooldowns
        ):
            self._last_signal.popitem(last=False)
        self._last_signal[key] = now
        self._last_signal.move_to_end(key)
        return Signal(detector, score, severity_for(score), reason)

    def analyze(self, message: dict[str, Any]) -> ModerationResult:
        text, entities = self._content(message)
        if not text.strip() or text.startswith("/"):
            return ModerationResult((), 0, "baja")

        chat_id = int(message["chat"]["id"])
        sender = message.get("from")
        if not isinstance(sender, dict) or sender.get("id") is None:
            return ModerationResult((), 0, "baja")
        user_id = int(sender["id"])
        now = self._event_timestamp(message)
        self._compact_state_if_due()
        signals: list[Signal] = []
        user_key = (chat_id, user_id)

        message_times = self._bounded_window(
            self._message_times,
            user_key,
            max_entries=self.max_user_windows,
        )
        self._insert_bounded(
            message_times,
            now,
            max(64, self.policy.flood_max_messages * 4),
        )
        self._prune(message_times, now, self.policy.flood_window_seconds)
        message_count = self._window_count(
            message_times, now, self.policy.flood_window_seconds
        )
        if message_count >= self.policy.flood_max_messages:
            signal = self._emit_once(
                chat_id=chat_id,
                user_id=user_id,
                detector="flood",
                now=now,
                score=45,
                reason=(
                    f"{message_count} mensajes en "
                    f"{self.policy.flood_window_seconds} segundos"
                ),
            )
            if signal:
                signals.append(signal)

        normalized = self._normalize(text)
        if len(normalized) >= self.policy.duplicate_minimum_length:
            fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            duplicate_key = (chat_id, user_id, fingerprint)
            duplicate_times = self._bounded_window(
                self._duplicate_times,
                duplicate_key,
                max_entries=self.max_duplicate_fingerprints,
            )
            self._insert_bounded(
                duplicate_times,
                now,
                max(16, self.policy.duplicate_max_repetitions * 4),
            )
            self._prune(
                duplicate_times, now, self.policy.duplicate_window_seconds
            )
            duplicate_count = self._window_count(
                duplicate_times, now, self.policy.duplicate_window_seconds
            )
            if duplicate_count >= self.policy.duplicate_max_repetitions:
                signal = self._emit_once(
                    chat_id=chat_id,
                    user_id=user_id,
                    detector="repeticion",
                    now=now,
                    score=55,
                    reason=(
                        f"mensaje repetido {duplicate_count} veces en "
                        f"{self.policy.duplicate_window_seconds} segundos"
                    ),
                )
                if signal:
                    signals.append(signal)

        if self._has_link(text, entities):
            link_times = self._bounded_window(
                self._link_times,
                user_key,
                max_entries=self.max_user_windows,
            )
            self._insert_bounded(
                link_times,
                now,
                max(32, self.policy.link_max_messages * 4),
            )
            self._prune(link_times, now, self.policy.link_window_seconds)
            link_count = self._window_count(
                link_times, now, self.policy.link_window_seconds
            )
            if link_count >= self.policy.link_max_messages:
                signal = self._emit_once(
                    chat_id=chat_id,
                    user_id=user_id,
                    detector="enlaces",
                    now=now,
                    score=40,
                    reason=(
                        f"{link_count} mensajes con enlaces en "
                        f"{self.policy.link_window_seconds} segundos"
                    ),
                )
                if signal:
                    signals.append(signal)

        unique_mentions = self._unique_mentions(text, entities)
        if len(unique_mentions) >= self.policy.mention_max_per_message:
            signal = self._emit_once(
                chat_id=chat_id,
                user_id=user_id,
                detector="menciones",
                now=now,
                score=35,
                reason=f"{len(unique_mentions)} menciones distintas en un mensaje",
            )
            if signal:
                signals.append(signal)

        total_score = min(100, sum(signal.score for signal in signals))
        return ModerationResult(
            tuple(signals), total_score, severity_for(total_score)
        )
