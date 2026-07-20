from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from telegram_guardian.config import ConfigurationError, Settings


class SettingsTests(unittest.TestCase):
    def test_loads_relative_database_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".env").write_text(
                "TELEGRAM_BOT_TOKEN=123:test-token-value\n"
                "TELEGRAM_BOT_USERNAME=Salome_G_BOT\n"
                "DATABASE_PATH=data/test.db\n",
                encoding="utf-8",
            )
            settings = Settings.load(root)

            self.assertEqual(settings.telegram_bot_username, "Salome_G_BOT")
            self.assertEqual(settings.database_path, root / "data" / "test.db")

    def test_rejects_empty_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".env").write_text(
                "TELEGRAM_BOT_TOKEN=\n"
                "TELEGRAM_BOT_USERNAME=Salome_G_BOT\n",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigurationError):
                Settings.load(root)


if __name__ == "__main__":
    unittest.main()

