from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_bg.config import CodexConfig
from codex_bg.executor import CodexExecutor
from codex_bg.models import Task, TaskStatus
from codex_bg.runner import CommandResult


class FakeRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def run(self, args, *, cwd=None, input_text=None, check=True):
        self.calls.append(list(args))
        out_file = args[args.index("-o") + 1]
        Path(out_file).write_text("{}", encoding="utf-8")
        return CommandResult(list(args), 0, '{"session_id":"session-1"}\n', "")


class ExecutorTests(unittest.TestCase):
    def test_approval_policy_is_passed_before_exec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            executor = CodexExecutor(runner, Path(tmp), CodexConfig(approval_policy="never"))
            task = Task(
                id=1,
                plugin_name="p",
                event_type="e",
                external_id="x",
                subject_id="s",
                prompt="prompt",
                payload={},
                workspace_key=None,
                dedupe_key="k",
                priority=100,
                status=TaskStatus.RUNNING,
                attempts=0,
                codex_session_id=None,
            )

            executor.run(task, None)

            args = runner.calls[0]
            self.assertEqual(args[:4], ["codex", "--ask-for-approval", "never", "exec"])

    def test_model_and_reasoning_effort_come_from_codex_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            executor = CodexExecutor(
                runner,
                Path(tmp),
                CodexConfig(model="gpt-main", reasoning_effort="high"),
            )
            task = Task(
                id=1,
                plugin_name="p",
                event_type="e",
                external_id="x",
                subject_id="s",
                prompt="prompt",
                payload={},
                workspace_key=None,
                dedupe_key="k",
                priority=100,
                status=TaskStatus.RUNNING,
                attempts=0,
                codex_session_id=None,
            )

            executor.run(task, None)

            args = runner.calls[0]
            self.assertEqual(args[args.index("--model") + 1], "gpt-main")
            self.assertIn('model_reasoning_effort="high"', args)

    def test_task_options_write_artifacts_schema_and_use_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            executor = CodexExecutor(runner, Path(tmp), CodexConfig())
            task = Task(
                id=1,
                plugin_name="p",
                event_type="e",
                external_id="x",
                subject_id="s",
                prompt="Read issue.json.",
                payload={"ignored": "not embedded"},
                workspace_key=None,
                dedupe_key="k",
                priority=100,
                status=TaskStatus.RUNNING,
                attempts=0,
                codex_session_id=None,
                executor_options={
                    "sandbox": "read-only",
                    "artifact_files": {"issue.json": {"title": "hello"}},
                    "output_schema": {"type": "object"},
                },
            )

            result = executor.run(task, None)

            artifact_dir = Path(result.artifact_dir)
            self.assertEqual(json_load(artifact_dir / "issue.json"), {"title": "hello"})
            self.assertEqual(json_load(artifact_dir / "output-schema.json"), {"type": "object"})
            self.assertIn("--output-schema", runner.calls[0])
            self.assertIn("read-only", runner.calls[0])
            prompt = (artifact_dir / "prompt.txt").read_text(encoding="utf-8")
            self.assertNotIn("Repository workspace policy:", prompt)

    def test_prompt_marks_repo_workspace_read_only_when_cwd_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            executor = CodexExecutor(runner, Path(tmp) / "runs", CodexConfig())
            repo = Path(tmp) / "repo"
            repo.mkdir()
            task = Task(
                id=1,
                plugin_name="p",
                event_type="e",
                external_id="x",
                subject_id="s",
                prompt="Inspect repo.",
                payload={},
                workspace_key="repo",
                dedupe_key="k",
                priority=100,
                status=TaskStatus.RUNNING,
                attempts=0,
                codex_session_id=None,
            )

            result = executor.run(task, repo)

            prompt = (Path(result.artifact_dir) / "prompt.txt").read_text(encoding="utf-8")
            self.assertIn("Repository workspace policy:", prompt)
            self.assertIn("shared read-only context", prompt)
            self.assertIn("separate git worktree", prompt)


def json_load(path: Path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
