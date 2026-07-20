"""Aplicacion inicial de Salomé en modo no administrativo."""

from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any

from .config import ConfigurationError, Settings
from .moderation import ObservationEngine
from .storage import Storage
from .telegram_api import TelegramApiError, TelegramClient


HELP_TEXT = (
    "Soy Salomé, asistente de seguridad del grupo.\n\n"
    "Comandos disponibles:\n"
    "/ayuda - muestra esta ayuda\n"
    "/estado - muestra mi modo actual\n"
    "/reglas - indica dónde estarán las reglas\n\n"
    "/riesgos - muestra señales recientes (solo administradores)\n\n"
    "Actualmente estoy en modo de pruebas y no realizo acciones administrativas."
)

MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60
STATE_MAINTENANCE_INTERVAL_SECONDS = 60
USER_COMMAND_COOLDOWN_SECONDS = 3
CHAT_RESPONSE_COOLDOWN_SECONDS = 1
CHAT_RESPONSE_LIMIT_PER_MINUTE = 15
GLOBAL_RESPONSE_LIMIT_PER_SECOND = 10
GLOBAL_RESPONSE_LIMIT_PER_MINUTE = 120
MAX_COMMAND_USER_ENTRIES = 10_000
MAX_COMMAND_CHAT_ENTRIES = 5_000


class SalomeApp:
    def __init__(
        self,
        settings: Settings,
        client: TelegramClient,
        storage: Storage,
        moderation: ObservationEngine,
        logger: logging.Logger,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        command_user_max_entries: int = MAX_COMMAND_USER_ENTRIES,
        command_chat_max_entries: int = MAX_COMMAND_CHAT_ENTRIES,
    ) -> None:
        self.settings = settings
        self.client = client
        self.storage = storage
        self.moderation = moderation
        self.logger = logger
        self._clock = clock
        self._wall_clock = wall_clock
        self.command_user_max_entries = max(100, command_user_max_entries)
        self.command_chat_max_entries = max(100, command_chat_max_entries)
        self._next_purge_at = self._clock() + MAINTENANCE_INTERVAL_SECONDS
        self._next_state_cleanup_at = (
            self._clock() + STATE_MAINTENANCE_INTERVAL_SECONDS
        )
        self._command_checks = 0
        self._last_user_command: OrderedDict[
            tuple[int, int], tuple[float, float]
        ] = OrderedDict()
        self._last_global_user_command: OrderedDict[
            int, tuple[float, float]
        ] = OrderedDict()
        self._last_chat_response: OrderedDict[int, float] = OrderedDict()
        self._chat_response_times: OrderedDict[
            int, deque[float]
        ] = OrderedDict()
        self._global_response_times: deque[float] = deque(
            maxlen=GLOBAL_RESPONSE_LIMIT_PER_MINUTE
        )
        self.can_read_all_group_messages = False

    def check_identity(self) -> dict[str, Any]:
        identity = self.client.get_me()
        actual_username = str(identity.get("username", ""))
        if actual_username.casefold() != self.settings.telegram_bot_username.casefold():
            raise ConfigurationError(
                "el token pertenece a un bot distinto del usuario configurado"
            )
        self.can_read_all_group_messages = bool(
            identity.get("can_read_all_group_messages", False)
        )
        self.logger.info("Identidad de Telegram validada para @%s", actual_username)
        return identity

    def run(self) -> None:
        offset = self.storage.get_offset()
        while True:
            try:
                self.run_state_maintenance_if_due()
                self.run_maintenance_if_due()
                updates = self.client.get_updates(offset)
                offset = self.process_updates(updates, offset)
            except TelegramApiError as exc:
                self.logger.warning("Fallo temporal durante polling: %s", exc)
                time.sleep(3)

    def process_updates(
        self, updates: list[dict[str, Any]], offset: int | None
    ) -> int | None:
        for update in updates:
            update_id = int(update["update_id"])
            try:
                self.handle_update(update)
            except TelegramApiError as exc:
                self.logger.warning(
                    "Update %s descartado tras fallo de Telegram: %s",
                    update_id,
                    exc,
                )
            except Exception:
                self.logger.exception(
                    "Update %s descartado por un fallo local", update_id
                )
            finally:
                offset = update_id + 1
                self.storage.set_offset(offset)
        return offset

    def run_maintenance_if_due(self) -> bool:
        now = self._clock()
        if now < self._next_purge_at:
            return False
        self.storage.purge_old_records(
            self.moderation.policy.signal_retention_days
        )
        self._next_purge_at = now + MAINTENANCE_INTERVAL_SECONDS
        self.logger.info("Mantenimiento diario de señales completado")
        return True

    def run_state_maintenance_if_due(self) -> bool:
        processing_now = self._clock()
        if processing_now < self._next_state_cleanup_at:
            return False
        self.moderation.compact(int(self._wall_clock()))
        self._compact_command_state(processing_now)
        self._next_state_cleanup_at = (
            processing_now + STATE_MAINTENANCE_INTERVAL_SECONDS
        )
        return True

    def _safe_message_time(self, value: object) -> float:
        wall_now = self._wall_clock()
        if isinstance(value, bool):
            return wall_now
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return wall_now
        if not math.isfinite(parsed):
            return wall_now
        return min(parsed, wall_now)

    def _command_input_allowed(
        self, chat_id: int, user_id: int, message_time: float
    ) -> bool:
        # message_time viene de Telegram. Usar el reloj de procesamiento aqui
        # permitiria que una respuesta HTTP lenta separase artificialmente dos
        # comandos que el usuario envio de inmediato.
        self._command_checks += 1
        processing_now = self._clock()
        if self._command_checks >= 256:
            self._command_checks = 0
            self._compact_command_state(processing_now)
        user_key = (chat_id, user_id)
        previous = self._last_user_command.get(user_key)
        global_previous = self._last_global_user_command.get(user_id)
        for key, prior, mapping in (
            (user_key, previous, self._last_user_command),
            (user_id, global_previous, self._last_global_user_command),
        ):
            if prior is None:
                continue
            previous_message_time, previous_processing_time = prior
            if (
                message_time - previous_message_time
                < USER_COMMAND_COOLDOWN_SECONDS
                or processing_now - previous_processing_time
                < USER_COMMAND_COOLDOWN_SECONDS
            ):
                mapping.move_to_end(key)
                return False

        if (
            user_key not in self._last_user_command
            and len(self._last_user_command) >= self.command_user_max_entries
        ):
            self._last_user_command.popitem(last=False)
        self._last_user_command[user_key] = (message_time, processing_now)
        self._last_user_command.move_to_end(user_key)
        if (
            user_id not in self._last_global_user_command
            and len(self._last_global_user_command)
            >= self.command_user_max_entries
        ):
            self._last_global_user_command.popitem(last=False)
        self._last_global_user_command[user_id] = (message_time, processing_now)
        self._last_global_user_command.move_to_end(user_id)
        return True

    def _output_allowed(self, chat_id: int) -> bool:
        processing_now = self._clock()
        boundary = processing_now - 60
        while (
            self._global_response_times
            and self._global_response_times[0] < boundary
        ):
            self._global_response_times.popleft()
        if len(self._global_response_times) >= GLOBAL_RESPONSE_LIMIT_PER_MINUTE:
            return False
        burst_count = sum(
            timestamp >= processing_now - 1
            for timestamp in self._global_response_times
        )
        if burst_count >= GLOBAL_RESPONSE_LIMIT_PER_SECOND:
            return False

        previous_chat_response = self._last_chat_response.get(chat_id)
        if (
            previous_chat_response is not None
            and processing_now - previous_chat_response
            < CHAT_RESPONSE_COOLDOWN_SECONDS
        ):
            return False

        chat_times = self._chat_response_times.get(chat_id)
        if chat_times is None:
            if len(self._chat_response_times) >= self.command_chat_max_entries:
                self._chat_response_times.popitem(last=False)
            chat_times = deque(maxlen=CHAT_RESPONSE_LIMIT_PER_MINUTE)
            self._chat_response_times[chat_id] = chat_times
        else:
            self._chat_response_times.move_to_end(chat_id)
        while chat_times and chat_times[0] < boundary:
            chat_times.popleft()
        if len(chat_times) >= CHAT_RESPONSE_LIMIT_PER_MINUTE:
            return False

        if (
            chat_id not in self._last_chat_response
            and len(self._last_chat_response) >= self.command_chat_max_entries
        ):
            self._last_chat_response.popitem(last=False)
        self._last_chat_response[chat_id] = processing_now
        self._last_chat_response.move_to_end(chat_id)
        chat_times.append(processing_now)
        self._global_response_times.append(processing_now)
        return True

    def _compact_command_state(self, processing_now: float) -> None:
        processing_boundary = processing_now - 60
        for key in list(self._last_user_command):
            if self._last_user_command[key][1] < processing_boundary:
                del self._last_user_command[key]
        for user_id in list(self._last_global_user_command):
            if self._last_global_user_command[user_id][1] < processing_boundary:
                del self._last_global_user_command[user_id]

        for chat_id in list(self._last_chat_response):
            if self._last_chat_response[chat_id] < processing_boundary:
                del self._last_chat_response[chat_id]
        for chat_id in list(self._chat_response_times):
            values = self._chat_response_times[chat_id]
            while values and values[0] < processing_boundary:
                values.popleft()
            if not values:
                del self._chat_response_times[chat_id]
        while (
            self._global_response_times
            and self._global_response_times[0] < processing_boundary
        ):
            self._global_response_times.popleft()

    def runtime_state_sizes(self) -> dict[str, int]:
        return {
            "command_users": len(self._last_user_command),
            "global_command_users": len(self._last_global_user_command),
            "command_chats": len(self._last_chat_response),
            "response_windows": len(self._chat_response_times),
            "global_responses": len(self._global_response_times),
        }

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return

        text = message.get("text")
        if not isinstance(text, str):
            return

        chat_id = int(message["chat"]["id"])
        user_id_value = message.get("from", {}).get("id")
        user_id = int(user_id_value) if user_id_value is not None else None
        update_id = int(update["update_id"])
        result = self.moderation.analyze(message)
        for signal in result.signals:
            self.storage.record_signal(
                update_id=update_id,
                chat_id=chat_id,
                user_id=user_id or 0,
                detector=signal.detector,
                score=signal.score,
                severity=signal.severity,
                reason=signal.reason,
            )
            self.logger.info(
                "Señal %s (%s) registrada en chat %s para usuario %s",
                signal.detector,
                signal.severity,
                chat_id,
                user_id,
            )

        if not text.startswith("/"):
            return

        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold()
        known_commands = {"/start", "/ayuda", "/estado", "/reglas", "/riesgos"}
        if command not in known_commands:
            return
        message_time = self._safe_message_time(message.get("date"))
        if user_id is None or not self._command_input_allowed(
            chat_id, user_id, message_time
        ):
            self.logger.debug("Comando limitado en chat %s", chat_id)
            return

        privacy_status = (
            "lectura de mensajes habilitada"
            if self.can_read_all_group_messages
            else "privacidad activa: solo recibo comandos y menciones"
        )

        responses = {
            "/start": HELP_TEXT,
            "/ayuda": HELP_TEXT,
            "/estado": (
                "Estado de Salomé: conexión activa, modo de pruebas, "
                f"sin permisos administrativos, sin IA y {privacy_status}."
            ),
            "/reglas": (
                "Las reglas del Laboratorio Salomé todavía están pendientes "
                "de aprobación por su administrador."
            ),
        }
        if command == "/riesgos":
            if message.get("chat", {}).get("type") not in {
                "group",
                "supergroup",
            }:
                self.logger.debug(
                    "Consulta de riesgos ignorada fuera de un grupo"
                )
                return
            is_admin = False
            if user_id is not None:
                try:
                    is_admin = self.client.is_chat_administrator(chat_id, user_id)
                except TelegramApiError as exc:
                    self.logger.warning(
                        "No se pudo comprobar administrador en chat %s: %s",
                        chat_id,
                        exc,
                    )
            if not is_admin:
                self.logger.debug(
                    "Consulta de riesgos no autorizada ignorada en chat %s",
                    chat_id,
                )
                return
            else:
                recent = self.storage.recent_signals(chat_id)
                if recent:
                    lines = ["Señales recientes en modo observación:"]
                    for item in recent:
                        lines.append(
                            f"• [{item['severity']}] {item['detector']}: "
                            f"{item['reason']} (usuario {item['user_id']})"
                        )
                    responses[command] = "\n".join(lines)
                else:
                    responses[command] = (
                        "No hay señales de riesgo registradas en este chat."
                    )
        response = responses.get(command)
        if response is None:
            return
        if not self._output_allowed(chat_id):
            self.logger.debug("Respuesta limitada en chat %s", chat_id)
            return

        self.client.send_message(chat_id, response)
        self.storage.record_event(
            update_id=update_id,
            event_type=f"command:{command.removeprefix('/')}",
            chat_id=chat_id,
            user_id=user_id,
        )
        self.logger.info("Comando %s atendido en chat %s", command, chat_id)
