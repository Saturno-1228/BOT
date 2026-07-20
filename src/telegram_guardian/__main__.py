"""Punto de entrada de Salomé."""

from __future__ import annotations

import argparse
import sys

from .app import SalomeApp
from .config import ConfigurationError, Settings
from .logging_setup import configure_logging
from .moderation import ModerationPolicy, ObservationEngine
from .storage import Storage
from .telegram_api import TelegramApiError, TelegramClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Salomé, bot de seguridad e IA")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="valida configuracion e identidad sin escuchar mensajes",
    )
    mode.add_argument(
        "--poll",
        action="store_true",
        help="inicia la escucha continua de mensajes",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        settings = Settings.load()
        logger = configure_logging(settings)
        storage = Storage(settings.database_path)
        storage.initialize()
        policy = ModerationPolicy.load(settings.root / "config" / "settings.json")
        storage.purge_old_records(policy.signal_retention_days)
        client = TelegramClient(settings.telegram_bot_token)
        moderation = ObservationEngine(policy)
        app = SalomeApp(settings, client, storage, moderation, logger)
        identity = app.check_identity()

        print(f"Conexion valida: @{identity['username']}")
        print(f"Base de datos: {settings.database_path}")
        privacy = (
            "desactivada; puede observar mensajes"
            if identity.get("can_read_all_group_messages")
            else "activada; solo recibe comandos y menciones"
        )
        print(f"Privacidad de grupos: {privacy}")

        if args.poll:
            print("Salomé esta escuchando. Presiona Ctrl+C para detenerla.")
            app.run()
        else:
            print("Modo comprobacion: no se leyeron mensajes del grupo.")
        return 0
    except (ConfigurationError, TelegramApiError) as exc:
        print(f"No se pudo iniciar Salomé: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Salomé fue detenida de forma segura.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
