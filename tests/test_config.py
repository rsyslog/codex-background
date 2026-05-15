from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_bg.config import load_config


class ConfigTests(unittest.TestCase):
    def test_default_codex_policy_is_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "scheduler.toml"
            config.write_text("", encoding="utf-8")

            app = load_config(config)

        self.assertEqual(app.codex.sandbox, "read-only")
        self.assertEqual(app.codex.approval_policy, "on-request")

    def test_plugin_interval_seconds_is_parsed_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "scheduler.toml"
            config.write_text(
                """
poll_interval_seconds = 900

[[plugins]]
name = "issue_triage"
module = "codex_bg.plugins.github_issue_triage"
interval_seconds = 60
custom_value = "kept"
""",
                encoding="utf-8",
            )

            app = load_config(config)

        self.assertEqual(app.plugins[0].interval_seconds, 60)
        self.assertEqual(app.plugins[0].values, {"custom_value": "kept"})


if __name__ == "__main__":
    unittest.main()
