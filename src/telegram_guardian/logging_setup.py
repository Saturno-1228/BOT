"""Registro local sin mensajes ni credenciales."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import Settings


def configure_logging(settings: Settings) -> logging.Logger:
    logs_path = settings.root / "logs"
    logs_path.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("salome")
    logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    file_handler = RotatingFileHandler(
        logs_path / "salome.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger
