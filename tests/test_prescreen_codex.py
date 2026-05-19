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
    def __init__(self, response: dict | None = None):
        self.calls: list[list[str]] = []
        self.timeouts: list[int | float | None] = []
        self.inputs: list[str | None] = []
        self.payloads: list[dict] = []
        self.cwd: list[Path | None] = []
        self.response = response or {"allowed": True, "reason": "related", "category": "support"}

    def run(self, args, *, cwd=None, input_text=None, check=True, timeout=None):
        self.calls.append(list(args))
        self.timeouts.append(timeout)
        self.inputs.append(input_text)
        self.cwd.append(Path(cwd) if cwd else None)
        if input_text:
            self.payloads.append(json.loads(_payload_file_from_prompt(input_text).read_text()))
        out_file = args[args.index("-o") + 1]
        Path(out_file).write_text(json.dumps(self.response), encoding="utf-8")
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
        self.assertIn("--skip-git-repo-check", runner.calls[0])
        self.assertIn("--ephemeral", runner.calls[0])
        self.assertIn("--ignore-rules", runner.calls[0])
        self.assertEqual(runner.timeouts, [20])
        self.assertIsNotNone(runner.cwd[0])
        self.assertTrue(runner.cwd[0].name.startswith("codex-bg-prescreen-"))
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

    def test_obvious_unrelated_question_is_decided_by_codex_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner(
                {"allowed": False, "reason": "not rsyslog related", "category": "off_topic"}
            )
            screener = CodexPreScreener(PreScreenConfig(), runner)  # type: ignore[arg-type]
            app = AppConfig(workdir_root=Path(tmp) / "workdirs")

            result = screener.screen(
                _context(app, runner),
                ScreeningRequest(
                    plugin_name="issue_triage",
                    subject_type="github_issue",
                    subject_id="rgerhards/rsyslog#205",
                    payload={
                        "repo": "rgerhards/rsyslog",
                        "issue": {
                            "title": "[Question]: How is the weather today",
                            "body": "I wonder if it is sunny or rains tomorrow.",
                        },
                    },
                    policy="Allow rsyslog issues only.",
                ),
            )

        self.assertFalse(result.allowed)
        self.assertIn("rsyslog", result.reason)
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("related to the configured repository/project", runner.inputs[0] or "")
        self.assertIn("Routing metadata", runner.inputs[0] or "")
        self.assertIn("abuse cybersecurity", runner.inputs[0] or "")

    def test_unrelated_software_question_is_decided_by_codex_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner(
                {"allowed": False, "reason": "not rsyslog related", "category": "off_topic"}
            )
            screener = CodexPreScreener(PreScreenConfig(), runner)  # type: ignore[arg-type]
            app = AppConfig(workdir_root=Path(tmp) / "workdirs")

            result = screener.screen(
                _context(app, runner),
                ScreeningRequest(
                    plugin_name="issue_triage",
                    subject_type="github_issue",
                    subject_id="rgerhards/rsyslog#207",
                    payload={
                        "repo": "rgerhards/rsyslog",
                        "issue": {
                            "title": "[Question]: How do I configure Apache?",
                            "body": "Can you explain Apache virtual hosts on Linux?",
                        },
                    },
                    policy="Allow rsyslog issues only.",
                ),
            )

        self.assertFalse(result.allowed)
        self.assertIn("rsyslog", result.reason)
        self.assertEqual(len(runner.calls), 1)

    def test_prescreen_payload_excludes_repository_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            screener = CodexPreScreener(PreScreenConfig(), runner)  # type: ignore[arg-type]
            app = AppConfig(workdir_root=Path(tmp) / "workdirs")

            screener.screen(
                _context(app, runner),
                ScreeningRequest(
                    plugin_name="issue_triage",
                    subject_type="github_issue",
                    subject_id="rgerhards/rsyslog#207",
                    payload={
                        "repo": "rgerhards/rsyslog",
                        "workspace_path": "/home/rger/aibot/rsyslog",
                        "issue": {
                            "number": 207,
                            "title": "How do I configure Apache?",
                            "body": "Can you explain virtual hosts?",
                            "labels": [{"name": "question"}],
                        },
                    },
                    policy="Allow rsyslog issues only.",
                ),
            )

        screened = runner.payloads[0]
        self.assertNotIn("repo", screened)
        self.assertNotIn("workspace_path", screened)
        self.assertNotIn("number", screened["issue"])
        self.assertNotIn("url", screened["issue"])
        self.assertEqual(screened["issue"]["title"], "How do I configure Apache?")
        self.assertEqual(screened["issue"]["labels"], ["question"])

    def test_weather_word_with_repo_context_falls_through_to_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            screener = CodexPreScreener(PreScreenConfig(), runner)  # type: ignore[arg-type]
            app = AppConfig(workdir_root=Path(tmp) / "workdirs")

            result = screener.screen(
                _context(app, runner),
                ScreeningRequest(
                    plugin_name="issue_triage",
                    subject_type="github_issue",
                    subject_id="rgerhards/rsyslog#206",
                    payload={
                        "repo": "rgerhards/rsyslog",
                        "issue": {
                            "title": "rsyslog weather sensor logs are malformed",
                            "body": "rsyslog parses these messages incorrectly.",
                        },
                    },
                    policy="Allow rsyslog issues only.",
                ),
            )

        self.assertTrue(result.allowed)
        self.assertEqual(len(runner.calls), 1)

    def test_empty_subject_is_rejected_without_codex_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            screener = CodexPreScreener(PreScreenConfig(), runner)  # type: ignore[arg-type]
            app = AppConfig(workdir_root=Path(tmp) / "workdirs")

            result = screener.screen(
                _context(app, runner),
                ScreeningRequest(
                    plugin_name="issue_triage",
                    subject_type="github_issue",
                    subject_id="rgerhards/rsyslog#208",
                    payload={"repo": "rgerhards/rsyslog", "issue": {"title": "", "body": ""}},
                    policy="Allow rsyslog issues only.",
                ),
            )

        self.assertFalse(result.allowed)
        self.assertEqual(runner.calls, [])


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


def _payload_file_from_prompt(prompt: str) -> Path:
    marker = "Read it from\n`"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("`", start)
    return Path(prompt[start:end])


if __name__ == "__main__":
    unittest.main()
