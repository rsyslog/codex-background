from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_bg.config import AppConfig, WorkspaceConfig
from codex_bg.models import Task, TaskStatus
from codex_bg.workspace import WorkspaceManager


class FakeRunner:
    def __init__(self, status: str = ""):
        self.calls: list[list[str]] = []
        self.status = status

    def run(self, args, *, cwd=None, input_text=None, check=True):
        self.calls.append(list(args))
        if args[:2] == ["git", "clone"]:
            path = Path(args[-1])
            (path / ".git").mkdir(parents=True)
        if args == ["git", "status", "--porcelain"]:
            from codex_bg.runner import CommandResult

            return CommandResult(list(args), 0, self.status, "")


class WorkspaceTests(unittest.TestCase):
    def test_workspace_uses_shared_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "aibot"
            app = AppConfig(
                workspace_root=root,
                workspaces={"repo": WorkspaceConfig(key="repo")},
            )
            task = Task(
                id=1,
                plugin_name="p",
                event_type="e",
                external_id="x",
                subject_id="s",
                prompt="p",
                payload={},
                workspace_key="repo",
                dedupe_key="k",
                priority=100,
                status=TaskStatus.RUNNING,
                attempts=0,
                codex_session_id=None,
            )

            path = WorkspaceManager(app, FakeRunner()).prepare(task)  # type: ignore[arg-type]

            self.assertEqual(path, root / "workspaces" / "repo")
            self.assertTrue(path.exists())

    def test_refresh_due_workspaces_clones_missing_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "aibot"
            runner = FakeRunner()
            app = AppConfig(
                workspace_root=root,
                workspaces={
                    "repo": WorkspaceConfig(
                        key="repo",
                        repo="https://example.invalid/repo.git",
                        branch="main",
                    )
                },
            )

            refreshed = WorkspaceManager(app, runner).refresh_due_workspaces()  # type: ignore[arg-type]

            self.assertEqual(refreshed, 1)
            self.assertIn(
                [
                    "git",
                    "clone",
                    "https://example.invalid/repo.git",
                    str(root / "workspaces" / "repo"),
                ],
                runner.calls,
            )
            self.assertTrue((root / "state" / "refresh" / "repo.stamp").exists())
            self.assertFalse((root / "workspaces" / "repo" / ".codex-bg-refresh").exists())

    def test_refresh_refuses_dirty_shared_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "aibot"
            repo = root / "workspaces" / "repo"
            (repo / ".git").mkdir(parents=True)
            app = AppConfig(
                workspace_root=root,
                workspaces={
                    "repo": WorkspaceConfig(
                        key="repo",
                        repo="https://example.invalid/repo.git",
                        branch="main",
                    )
                },
            )

            with self.assertRaisesRegex(Exception, "shared workspace repo is dirty"):
                WorkspaceManager(app, FakeRunner(status=" M file.c\n")).refresh_due_workspaces()  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
