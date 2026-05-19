from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codex_bg.config import AppConfig, PluginConfig
from codex_bg.models import AiResult, Task, TaskStatus
from codex_bg.plugin import PluginContext
from codex_bg.plugins.github_issue_triage import AI_REVIEW_FOOTER, create_plugin
from codex_bg.prescreen import PreScreenContext, ScreeningRequest, ScreeningResult
from codex_bg.runner import CommandError, CommandResult
from codex_bg.store import Store


class FakeRunner:
    def __init__(self, issues: list[dict] | None = None):
        self.issues = issues or []
        self.comments: list[str] = []
        self.calls: list[list[str]] = []

    def run(self, args, *, cwd=None, input_text=None, check=True):
        self.calls.append(list(args))
        if args[:3] == ["gh", "issue", "list"]:
            return CommandResult(list(args), 0, json.dumps(self.issues), "")
        if args[:3] == ["gh", "issue", "view"]:
            return CommandResult(
                list(args),
                0,
                json.dumps({"comments": [{"body": body} for body in self.comments]}),
                "",
            )
        if args[:3] == ["gh", "issue", "comment"]:
            body_file = args[args.index("--body-file") + 1]
            self.comments.append(Path(body_file).read_text(encoding="utf-8"))
        return CommandResult(list(args), 0, "", "")


class FailingEditRunner(FakeRunner):
    def run(self, args, *, cwd=None, input_text=None, check=True):
        if args[:3] == ["gh", "issue", "edit"]:
            self.calls.append(list(args))
            raise CommandError(CommandResult(list(args), 1, "", "edit failed"))
        return super().run(args, cwd=cwd, input_text=input_text, check=check)


class FakePreScreener:
    def __init__(self, allowed: bool):
        self.allowed = allowed
        self.requests: list[ScreeningRequest] = []

    def screen(self, context: PreScreenContext, request: ScreeningRequest) -> ScreeningResult:
        self.requests.append(request)
        return ScreeningResult(self.allowed, "test decision")


class GitHubIssueTriageTests(unittest.TestCase):
    def test_plugin_sets_conservative_default_rate_limits(self) -> None:
        plugin = create_plugin(_plugin_config("unused.md"))

        self.assertEqual(plugin.default_rate_limit_per_hour, 15)
        self.assertEqual(plugin.default_rate_limit_per_day, 30)

    def test_generate_events_skips_triaged_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(str(instructions))
            plugin = create_plugin(config)
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "new",
                        "body": "body",
                        "labels": [],
                        "updatedAt": "2026-05-15T00:00:00Z",
                    },
                    {
                        "number": 2,
                        "title": "done",
                        "body": "body",
                        "labels": [{"name": "codex-triaged"}],
                        "updatedAt": "2026-05-15T00:00:00Z",
                    },
                ]
            )

            events = plugin.generate_events(PluginContext(AppConfig(), config, runner))  # type: ignore[arg-type]

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].subject_id, "owner/repo#1")
            self.assertEqual(events[0].workspace_key, "main")
            self.assertEqual(events[0].executor_options["sandbox"], "read-only")
            self.assertIn("issue.json", events[0].executor_options["artifact_files"])
            self.assertIn("output_schema", events[0].executor_options)

    def test_generate_events_stores_and_uses_repo_update_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(str(instructions))
            plugin = create_plugin(config)
            store = Store(Path(tmp) / "state.sqlite3")
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "first",
                        "body": "body",
                        "labels": [],
                        "updatedAt": "2026-05-15T01:00:00Z",
                    }
                ]
            )
            context = PluginContext(AppConfig(), config, runner, store=store)  # type: ignore[arg-type]

            first = plugin.generate_events(context)
            second = plugin.generate_events(context)

            self.assertEqual([event.subject_id for event in first], ["owner/repo#1"])
            self.assertEqual(second, [])
            self.assertEqual(
                store.get_plugin_state(
                    "issue_triage",
                    "github_issue_triage:owner/repo:last_seen_updated_at",
                ),
                "2026-05-15T01:00:00Z",
            )

    def test_generate_events_allows_issue_newer_than_repo_update_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(str(instructions))
            plugin = create_plugin(config)
            store = Store(Path(tmp) / "state.sqlite3")
            store.set_plugin_state(
                "issue_triage",
                "github_issue_triage:owner/repo:last_seen_updated_at",
                "2026-05-15T01:00:00Z",
            )
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "old",
                        "body": "body",
                        "labels": [],
                        "updatedAt": "2026-05-15T01:00:00Z",
                    },
                    {
                        "number": 2,
                        "title": "new",
                        "body": "body",
                        "labels": [],
                        "updatedAt": "2026-05-15T01:05:00Z",
                    },
                ]
            )

            events = plugin.generate_events(
                PluginContext(AppConfig(), config, runner, store=store)  # type: ignore[arg-type]
            )

            self.assertEqual([event.subject_id for event in events], ["owner/repo#2"])
            self.assertEqual(
                store.get_plugin_state(
                    "issue_triage",
                    "github_issue_triage:owner/repo:last_seen_updated_at",
                ),
                "2026-05-15T01:05:00Z",
            )

    def test_generate_events_does_not_prescreen_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(str(instructions))
            plugin = create_plugin(config)
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "new",
                        "body": "body",
                        "labels": [],
                        "updatedAt": "2026-05-15T00:00:00Z",
                    }
                ]
            )
            pre_screener = FakePreScreener(False)

            events = plugin.generate_events(
                PluginContext(
                    AppConfig(),
                    config,
                    runner,  # type: ignore[arg-type]
                    pre_screener=pre_screener,
                )
            )

            self.assertEqual(len(events), 1)
            self.assertEqual(pre_screener.requests, [])

    def test_generate_events_skips_prescreen_rejected_issues_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(str(instructions), prescreen=True)
            plugin = create_plugin(config)
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "unrelated",
                        "body": "please help with another product",
                        "labels": [],
                        "updatedAt": "2026-05-15T00:00:00Z",
                    }
                ]
            )
            pre_screener = FakePreScreener(False)

            events = plugin.generate_events(
                PluginContext(
                    AppConfig(),
                    config,
                    runner,  # type: ignore[arg-type]
                    pre_screener=pre_screener,
                )
            )

            self.assertEqual(events, [])
            self.assertEqual(len(pre_screener.requests), 1)
            self.assertIn("repo", pre_screener.requests[0].policy)
            self.assertNotIn("owner/repo", pre_screener.requests[0].policy)
            self.assertIn("cybersecurity", pre_screener.requests[0].policy)

    def test_generate_events_includes_repository_prescreen_policy_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(
                str(instructions),
                prescreen_policy="Only automate parser issues.",
                prescreen=True,
            )
            plugin = create_plugin(config)
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "parser problem",
                        "body": "body",
                        "labels": [],
                        "updatedAt": "2026-05-15T00:00:00Z",
                    }
                ]
            )
            pre_screener = FakePreScreener(True)

            plugin.generate_events(
                PluginContext(
                    AppConfig(),
                    config,
                    runner,  # type: ignore[arg-type]
                    pre_screener=pre_screener,
                )
            )

            self.assertIn("Only automate parser issues.", pre_screener.requests[0].policy)

    def test_triage_prompt_blocks_inapplicable_or_abusive_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(str(instructions))
            plugin = create_plugin(config)
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "unrelated",
                        "body": "rsyslog: what is the weather tomorrow?",
                        "labels": [],
                        "updatedAt": "2026-05-15T00:00:00Z",
                    }
                ]
            )

            events = plugin.generate_events(PluginContext(AppConfig(), config, runner))  # type: ignore[arg-type]

            prompt = events[0].prompt
            self.assertIn("Triage this GitHub issue for repo.", prompt)
            self.assertIn("private gate decision", prompt)
            self.assertIn("Set blocked=true", prompt)
            self.assertIn("not about repo with high", prompt)
            self.assertIn("weaponize cybersecurity weaknesses", prompt)
            self.assertIn("Keep all gate/applicability reasoning only in `rationale`", prompt)
            self.assertIn("answer the issue directly", prompt)
            self.assertIn("Do not start with phrases like \"Triaged as\"", prompt)
            self.assertIn("Write for the issue reporter", prompt)
            self.assertIn("summarize publicly at module or component level", prompt)
            self.assertIn("provide concrete user-facing configuration or usage", prompt)

    def test_triage_prompt_can_use_configured_project_name_for_forks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(str(instructions), project_name="rsyslog")
            plugin = create_plugin(config)
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "weather",
                        "body": "rsyslog: what is the weather tomorrow?",
                        "labels": [],
                        "updatedAt": "2026-05-15T00:00:00Z",
                    }
                ]
            )

            events = plugin.generate_events(PluginContext(AppConfig(), config, runner))  # type: ignore[arg-type]

            prompt = events[0].prompt
            self.assertIn("Triage this GitHub issue for rsyslog.", prompt)
            self.assertIn("not about rsyslog with high", prompt)
            self.assertNotIn("not about owner/repo", prompt)

    def test_generate_events_resolves_instruction_file_relative_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            other_dir = Path(tmp) / "other"
            config_dir.mkdir()
            other_dir.mkdir()
            (config_dir / "triage.md").write_text(
                "Use config-relative instructions.",
                encoding="utf-8",
            )
            (other_dir / "triage.md").write_text("Wrong instructions.", encoding="utf-8")
            config = _plugin_config("triage.md", base_dir=config_dir)
            plugin = create_plugin(config)
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "new",
                        "body": "body",
                        "labels": [],
                        "updatedAt": "2026-05-15T00:00:00Z",
                    }
                ]
            )
            old_cwd = Path.cwd()
            try:
                import os

                os.chdir(other_dir)
                events = plugin.generate_events(PluginContext(AppConfig(), config, runner))  # type: ignore[arg-type]
            finally:
                os.chdir(old_cwd)

            self.assertIn("Use config-relative instructions.", events[0].prompt)
            self.assertNotIn("Wrong instructions.", events[0].prompt)

    def test_generate_events_can_override_triage_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(str(instructions), sandbox="danger-full-access")
            plugin = create_plugin(config)
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "new",
                        "body": "body",
                        "labels": [],
                        "updatedAt": "2026-05-15T00:00:00Z",
                    }
                ]
            )

            events = plugin.generate_events(PluginContext(AppConfig(), config, runner))  # type: ignore[arg-type]

            self.assertEqual(events[0].executor_options["sandbox"], "danger-full-access")

    def test_generate_events_skips_issues_older_than_configured_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instructions = Path(tmp) / "triage.md"
            instructions.write_text("Be concise.", encoding="utf-8")
            config = _plugin_config(str(instructions), max_issue_age_days=7)
            plugin = create_plugin(config)
            now = datetime.now(UTC)
            runner = FakeRunner(
                [
                    {
                        "number": 1,
                        "title": "new",
                        "body": "body",
                        "labels": [],
                        "createdAt": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                        "updatedAt": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                    },
                    {
                        "number": 2,
                        "title": "old",
                        "body": "body",
                        "labels": [],
                        "createdAt": (now - timedelta(days=30)).isoformat().replace("+00:00", "Z"),
                        "updatedAt": now.isoformat().replace("+00:00", "Z"),
                    },
                ]
            )

            events = plugin.generate_events(PluginContext(AppConfig(), config, runner))  # type: ignore[arg-type]

            self.assertEqual([event.subject_id for event in events], ["owner/repo#1"])

    def test_handle_result_applies_allowlisted_metadata_and_marker(self) -> None:
        config = _plugin_config("unused.md")
        plugin = create_plugin(config)
        runner = FakeRunner()
        task = Task(
            id=1,
            plugin_name="issue_triage",
            event_type="github_issue_triage",
            external_id="owner/repo#1",
            subject_id="owner/repo#1",
            prompt="triage",
            payload={
                "repo": "owner/repo",
                "issue": {"number": 1},
                "triaged_label": "codex-triaged",
                "allowed_labels": ["bug"],
                "allowed_milestones": ["backlog"],
            },
            workspace_key="main",
            dedupe_key="k",
            priority=100,
            status=TaskStatus.RUNNING,
            attempts=0,
            codex_session_id=None,
        )
        result = AiResult(
            task_id=1,
            status="complete",
            final_message="fallback",
            structured={
                "comment": "This looks like a bug.",
                "labels": ["bug", "not-allowed"],
                "milestone": "backlog",
            },
            codex_session_id="session-1",
            artifact_dir="artifacts",
        )

        plugin.handle_result(PluginContext(AppConfig(), config, runner), task, result)  # type: ignore[arg-type]

        self.assertEqual(len(runner.comments), 1)
        self.assertIn(AI_REVIEW_FOOTER, runner.comments[0])
        self.assertIn(
            ["gh", "issue", "edit", "1", "--repo", "owner/repo", "--add-label", "bug"], runner.calls
        )
        self.assertIn(
            ["gh", "issue", "edit", "1", "--repo", "owner/repo", "--milestone", "backlog"],
            runner.calls,
        )
        self.assertIn(
            ["gh", "issue", "edit", "1", "--repo", "owner/repo", "--add-label", "codex-triaged"],
            runner.calls,
        )
        self.assertNotIn(
            ["gh", "issue", "edit", "1", "--repo", "owner/repo", "--add-label", "not-allowed"],
            runner.calls,
        )

    def test_handle_result_retry_does_not_duplicate_comment(self) -> None:
        config = _plugin_config("unused.md")
        plugin = create_plugin(config)
        runner = FailingEditRunner()
        task = Task(
            id=1,
            plugin_name="issue_triage",
            event_type="github_issue_triage",
            external_id="owner/repo#1",
            subject_id="owner/repo#1",
            prompt="triage",
            payload={
                "repo": "owner/repo",
                "issue": {"number": 1},
                "triaged_label": "codex-triaged",
                "allowed_labels": ["bug"],
                "allowed_milestones": [],
            },
            workspace_key="main",
            dedupe_key="k",
            priority=100,
            status=TaskStatus.RUNNING,
            attempts=0,
            codex_session_id=None,
        )
        result = AiResult(
            task_id=1,
            status="complete",
            final_message="fallback",
            structured={"comment": "This looks like a bug.", "labels": ["bug"]},
            codex_session_id="session-1",
            artifact_dir="artifacts",
        )

        with self.assertRaises(CommandError):
            plugin.handle_result(PluginContext(AppConfig(), config, runner), task, result)  # type: ignore[arg-type]
        with self.assertRaises(CommandError):
            plugin.handle_result(PluginContext(AppConfig(), config, runner), task, result)  # type: ignore[arg-type]

        self.assertEqual(len(runner.comments), 1)

    def test_handle_result_uses_stable_comment_marker_across_updated_issue_tasks(self) -> None:
        config = _plugin_config("unused.md")
        plugin = create_plugin(config)
        runner = FailingEditRunner()
        first = _triage_task(dedupe_key="issue_triage:owner/repo#1:old")
        second = _triage_task(dedupe_key="issue_triage:owner/repo#1:new")
        result = AiResult(
            task_id=1,
            status="complete",
            final_message="fallback",
            structured={"comment": "This looks like a bug.", "labels": ["bug"]},
            codex_session_id="session-1",
            artifact_dir="artifacts",
        )

        with self.assertRaises(CommandError):
            plugin.handle_result(PluginContext(AppConfig(), config, runner), first, result)  # type: ignore[arg-type]
        with self.assertRaises(CommandError):
            plugin.handle_result(PluginContext(AppConfig(), config, runner), second, result)  # type: ignore[arg-type]

        self.assertEqual(len(runner.comments), 1)
        self.assertIn("<!-- codex-bg:triage:owner/repo#1 -->", runner.comments[0])

    def test_handle_result_renders_references_before_footer(self) -> None:
        config = _plugin_config("unused.md")
        plugin = create_plugin(config)
        runner = FakeRunner()
        task = Task(
            id=1,
            plugin_name="issue_triage",
            event_type="github_issue_triage",
            external_id="owner/repo#1",
            subject_id="owner/repo#1",
            prompt="triage",
            payload={
                "repo": "owner/repo",
                "issue": {"number": 1},
                "triaged_label": "codex-triaged",
                "allowed_labels": [],
                "allowed_milestones": [],
            },
            workspace_key="main",
            dedupe_key="k",
            priority=100,
            status=TaskStatus.RUNNING,
            attempts=0,
            codex_session_id=None,
        )
        result = AiResult(
            task_id=1,
            status="complete",
            final_message="fallback",
            structured={
                "comment": "See docs.",
                "labels": [],
                "milestone": None,
                "references": ["https://docs.rsyslog.com/configuration/modules/imfile.html"],
            },
            codex_session_id="session-1",
            artifact_dir="artifacts",
        )

        plugin.handle_result(PluginContext(AppConfig(), config, runner), task, result)  # type: ignore[arg-type]

        self.assertIn("References:", runner.comments[0])
        self.assertIn(
            "https://docs.rsyslog.com/configuration/modules/imfile.html", runner.comments[0]
        )
        self.assertTrue(runner.comments[0].rstrip().endswith(AI_REVIEW_FOOTER))

    def test_dry_run_does_not_post_comment_or_edit_issue(self) -> None:
        config = _plugin_config("unused.md")
        plugin = create_plugin(config)
        runner = FakeRunner()
        task = Task(
            id=1,
            plugin_name="issue_triage",
            event_type="github_issue_triage",
            external_id="owner/repo#1",
            subject_id="owner/repo#1",
            prompt="triage",
            payload={
                "repo": "owner/repo",
                "issue": {"number": 1},
                "triaged_label": "codex-triaged",
                "allowed_labels": ["bug"],
                "allowed_milestones": ["backlog"],
            },
            workspace_key="main",
            dedupe_key="k",
            priority=100,
            status=TaskStatus.RUNNING,
            attempts=0,
            codex_session_id=None,
        )
        result = AiResult(
            task_id=1,
            status="complete",
            final_message="fallback",
            structured={
                "comment": "This looks like a bug.",
                "labels": ["bug"],
                "milestone": "backlog",
            },
            codex_session_id="session-1",
            artifact_dir="artifacts",
        )

        plugin.handle_result(
            PluginContext(AppConfig(dry_run=True), config, runner),  # type: ignore[arg-type]
            task,
            result,
        )

        self.assertEqual(runner.comments, [])
        self.assertNotIn(
            ["gh", "issue", "edit", "1", "--repo", "owner/repo", "--add-label", "bug"], runner.calls
        )
        self.assertNotIn(
            ["gh", "issue", "edit", "1", "--repo", "owner/repo", "--add-label", "codex-triaged"],
            runner.calls,
        )


def _plugin_config(
    instructions_file: str,
    max_issue_age_days: int | None = None,
    sandbox: str | None = None,
    base_dir: Path | None = None,
    prescreen_policy: str | None = None,
    prescreen: bool | None = None,
    project_name: str | None = None,
) -> PluginConfig:
    repo_config = {
        "repo": "owner/repo",
        "workspace_key": "main",
        "instructions_file": instructions_file,
        "triaged_label": "codex-triaged",
        "allowed_labels": ["bug", "question"],
        "allowed_milestones": ["backlog"],
    }
    if max_issue_age_days is not None:
        repo_config["max_issue_age_days"] = max_issue_age_days
    if sandbox is not None:
        repo_config["sandbox"] = sandbox
    if prescreen_policy is not None:
        repo_config["prescreen_policy"] = prescreen_policy
    if prescreen is not None:
        repo_config["prescreen"] = prescreen
    if project_name is not None:
        repo_config["project_name"] = project_name
    return PluginConfig(
        name="issue_triage",
        module="codex_bg.plugins.github_issue_triage",
        base_dir=base_dir or Path("."),
        values={"repos": [repo_config]},
    )


def _triage_task(dedupe_key: str = "k") -> Task:
    return Task(
        id=1,
        plugin_name="issue_triage",
        event_type="github_issue_triage",
        external_id="owner/repo#1",
        subject_id="owner/repo#1",
        prompt="triage",
        payload={
            "repo": "owner/repo",
            "issue": {"number": 1},
            "triaged_label": "codex-triaged",
            "allowed_labels": ["bug"],
            "allowed_milestones": [],
        },
        workspace_key="main",
        dedupe_key=dedupe_key,
        priority=100,
        status=TaskStatus.RUNNING,
        attempts=0,
        codex_session_id=None,
    )


if __name__ == "__main__":
    unittest.main()
