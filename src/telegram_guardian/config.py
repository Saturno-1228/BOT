"""Carga de configuracion portable y validacion de secretos."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(RuntimeError):
    """Indica que falta una opcion necesaria o que su valor no es valido."""


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ConfigurationError(f"No existe el archivo privado: {path}")

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class Settings:
    root: Path
    telegram_bot_token: str
    telegram_bot_username: str
    database_path: Path
    log_level: str

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        root = (root or project_root()).resolve()
        file_values = load_env_file(root / ".env")

        def value(name: str, default: str = "") -> str:
            return os.environ.get(name, file_values.get(name, default)).strip()

        token = value("TELEGRAM_BOT_TOKEN")
        username = value("TELEGRAM_BOT_USERNAME").removeprefix("@")
        if not token or ":" not in token:
            raise ConfigurationError(
                "TELEGRAM_BOT_TOKEN esta vacio o no tiene el formato esperado"
            )
        if not username:
            raise ConfigurationError("TELEGRAM_BOT_USERNAME esta vacio")

        database_value = value("DATABASE_PATH", "data/guardian.db")
        database_path = Path(database_value)
        if not database_path.is_absolute():
            database_path = root / database_path

        return cls(
            root=root,
            telegram_bot_token=token,
            telegram_bot_username=username,
            database_path=database_path.resolve(),
            log_level=value("LOG_LEVEL", "INFO").upper(),
        )

