from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bg import cli


class CliTests(unittest.TestCase):
    def test_config_is_accepted_after_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "scheduler.toml"
            config.write_text("plugins = []\n", encoding="utf-8")
            with patch("codex_bg.cli.Scheduler") as scheduler, patch("sys.stdout", io.StringIO()):
                scheduler.return_value.once.return_value = {"generated": 0, "worked": False}

                code = cli.main(["once", "--config", str(config)])

        self.assertEqual(code, 0)

    def test_debug_is_accepted_after_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "scheduler.toml"
            config.write_text("plugins = []\n", encoding="utf-8")
            with patch("codex_bg.cli.Scheduler") as scheduler, patch("sys.stdout", io.StringIO()):
                scheduler.return_value.once.return_value = {"generated": 0, "worked": False}

                code = cli.main(["once", "--config", str(config), "--debug"])

        self.assertEqual(code, 0)
        app = scheduler.call_args.args[0]
        self.assertTrue(app.debug)

    def test_dry_run_is_accepted_after_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "scheduler.toml"
            config.write_text("plugins = []\n", encoding="utf-8")
            with patch("codex_bg.cli.Scheduler") as scheduler, patch("sys.stdout", io.StringIO()):
                scheduler.return_value.once.return_value = {"generated": 0, "worked": False}

                code = cli.main(["once", "--config", str(config), "--dry-run"])

        self.assertEqual(code, 0)
        app = scheduler.call_args.args[0]
        self.assertTrue(app.dry_run)

    def test_force_is_accepted_for_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "scheduler.toml"
            config.write_text("plugins = []\n", encoding="utf-8")
            with patch("codex_bg.cli.Scheduler") as scheduler, patch("sys.stdout", io.StringIO()):
                scheduler.return_value.once.return_value = {"generated": 0, "worked": False}

                code = cli.main(["once", "--config", str(config), "--force"])

        self.assertEqual(code, 0)
        scheduler.return_value.once.assert_called_once_with(force=True)


if __name__ == "__main__":
    unittest.main()
