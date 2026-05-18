from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_bg.config import AppConfig, CodexConfig, PreScreenConfig
from codex_bg.prescreen import PreScreenContext, ScreeningRequest
from codex_bg.prescreen_codex import CodexPreScreener
from codex_bg.runner import CommandResult


class FakeRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def run(self, args, *, cwd=None, input_text=None, check=True):
        self.calls.append(list(args))
        out_file = args[args.index("-o") + 1]
        Path(out_file).write_text(
            json.dumps({"allowed": True, "reason": "related", "category": "support"}),
            encoding="utf-8",
        )
        return CommandResult(list(args), 0, '{"session_id":"session-1"}\n', "")


class CodexPreScreenerTests(unittest.TestCase):
    def test_uses_default_small_model_when_no_model_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            screener = CodexPreScreener(PreScreenConfig(), runner)  # type: ignore[arg-type]
            app = AppConfig(workdir_root=Path(tmp) / "workdirs")

            result = screener.screen(_context(app, runner), _request())

        self.assertTrue(result.allowed)
        self.assertIn("--model", runner.calls[0])
        self.assertEqual(runner.calls[0][runner.calls[0].index("--model") + 1], "gpt-5.4-mini")
        self.assertIn('model_reasoning_effort="medium"', runner.calls[0])

    def test_inherits_main_codex_model_when_prescreen_model_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            screener = CodexPreScreener(PreScreenConfig(), runner)  # type: ignore[arg-type]
            app = AppConfig(
                workdir_root=Path(tmp) / "workdirs",
                codex=CodexConfig(model="gpt-main", reasoning_effort="high"),
            )

            screener.screen(_context(app, runner), _request())

        self.assertEqual(runner.calls[0][runner.calls[0].index("--model") + 1], "gpt-main")
        self.assertIn('model_reasoning_effort="high"', runner.calls[0])

    def test_prescreen_config_overrides_main_codex_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            screener = CodexPreScreener(
                PreScreenConfig(model="gpt-screen", reasoning_effort="low"),
                runner,  # type: ignore[arg-type]
            )
            app = AppConfig(
                workdir_root=Path(tmp) / "workdirs",
                codex=CodexConfig(model="gpt-main", reasoning_effort="high"),
            )

            screener.screen(_context(app, runner), _request())

        self.assertEqual(runner.calls[0][runner.calls[0].index("--model") + 1], "gpt-screen")
        self.assertIn('model_reasoning_effort="low"', runner.calls[0])


def _context(app: AppConfig, runner: FakeRunner) -> PreScreenContext:
    return PreScreenContext(app, runner)  # type: ignore[arg-type]


def _request() -> ScreeningRequest:
    return ScreeningRequest(
        plugin_name="issue_triage",
        subject_type="github_issue",
        subject_id="owner/repo#1",
        payload={"issue": {"title": "test"}},
        policy="Allow related issues only.",
    )


if __name__ == "__main__":
    unittest.main()
