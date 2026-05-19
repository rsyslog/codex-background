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

    def test_plugin_scheduler_options_are_parsed_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "scheduler.toml"
            config.write_text(
                """
poll_interval_seconds = 900

[[plugins]]
name = "issue_triage"
module = "codex_bg.plugins.github_issue_triage"
interval_seconds = 60
rate_limit_per_hour = 10
rate_limit_per_day = 20
custom_value = "kept"
""",
                encoding="utf-8",
            )

            app = load_config(config)

        self.assertEqual(app.plugins[0].interval_seconds, 60)
        self.assertEqual(app.plugins[0].rate_limit_per_hour, 10)
        self.assertEqual(app.plugins[0].rate_limit_per_day, 20)
        self.assertEqual(app.plugins[0].base_dir, Path(tmp))
        self.assertEqual(app.plugins[0].values, {"custom_value": "kept"})

    def test_codex_and_prescreen_model_config_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "scheduler.toml"
            config.write_text(
                """
[codex]
model = "gpt-main"
reasoning_effort = "high"

[prescreen]
module = "codex_bg.prescreen_codex"
model = "gpt-screen"
reasoning_effort = "medium"
timeout_seconds = 7
custom_value = "kept"
""",
                encoding="utf-8",
            )

            app = load_config(config)

        self.assertEqual(app.codex.model, "gpt-main")
        self.assertEqual(app.codex.reasoning_effort, "high")
        self.assertEqual(app.prescreen.module, "codex_bg.prescreen_codex")
        self.assertEqual(app.prescreen.model, "gpt-screen")
        self.assertEqual(app.prescreen.reasoning_effort, "medium")
        self.assertEqual(app.prescreen.timeout_seconds, 7)
        self.assertEqual(app.prescreen.values, {"custom_value": "kept"})


if __name__ == "__main__":
    unittest.main()
