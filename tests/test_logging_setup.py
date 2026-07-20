from __future__ import annotations

import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram_guardian.config import Settings
from telegram_guardian.logging_setup import configure_logging


class LoggingSetupTests(unittest.TestCase):
    def test_log_file_is_rotated_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings(
                root=root,
                telegram_bot_token="123:test",
                telegram_bot_username="Salome_G_BOT",
                database_path=root / "data" / "test.db",
                log_level="INFO",
            )
            logger = configure_logging(settings)
            handler = logger.handlers[0]

            self.assertIsInstance(handler, RotatingFileHandler)
            self.assertEqual(handler.maxBytes, 5 * 1024 * 1024)
            self.assertEqual(handler.backupCount, 3)
            handler.close()
            logger.handlers.clear()


if __name__ == "__main__":
    unittest.main()
