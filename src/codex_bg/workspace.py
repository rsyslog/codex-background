from __future__ import annotations

from pathlib import Path
from typing import Callable
import time
import re

from codex_bg.config import AppConfig, WorkspaceConfig
from codex_bg.models import Task
from codex_bg.runner import Runner


class WorkspaceError(RuntimeError):
    pass


class WorkspaceManager:
    def __init__(
        self,
        app: AppConfig,
        runner: Runner,
        debug: Callable[[str], None] = lambda message: None,
    ):
        self.app = app
        self.runner = runner
        self.debug = debug

    def prepare(self, task: Task) -> Path | None:
        if not task.workspace_key:
            self.debug(f"task {task.id} does not require a workspace")
            return None
        workspace = self.app.workspaces.get(task.workspace_key)
        if workspace is None:
            raise WorkspaceError(f"unknown workspace_key: {task.workspace_key}")
        return self._prepare_workspace(workspace, force_refresh=True)

    def refresh_due_workspaces(self) -> int:
        refreshed = 0
        self.app.workspace_root.mkdir(parents=True, exist_ok=True)
        for workspace in self.app.workspaces.values():
            if not workspace.repo:
                continue
            path = self._workspace_path(workspace)
            if not self._refresh_due(path):
                self.debug(f"workspace {workspace.key} refresh not due")
                continue
            self._prepare_workspace(workspace, force_refresh=True)
            self._write_refresh_stamp(path)
            refreshed += 1
        return refreshed

    def _prepare_workspace(self, workspace: WorkspaceConfig, *, force_refresh: bool) -> Path:
        path = self._workspace_path(workspace)
        if workspace.repo and not (path / ".git").exists():
            self.debug(f"cloning workspace {workspace.key} from {workspace.repo}")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.runner.run(["git", "clone", workspace.repo, str(path)])
        elif not workspace.repo:
            self.debug(f"creating local workspace {workspace.key} at {path}")
            path.mkdir(parents=True, exist_ok=True)

        if workspace.repo and force_refresh:
            self._ensure_clean(path, workspace.key)
            self.debug(f"updating workspace {workspace.key} at {path}")
            self.runner.run(["git", "fetch", "--all", "--prune"], cwd=path)
            self.runner.run(["git", "checkout", workspace.branch], cwd=path)
            self.runner.run(["git", "pull", "--ff-only"], cwd=path)
        return path

    def _workspace_path(self, workspace: WorkspaceConfig) -> Path:
        return self.app.workspace_root / "workspaces" / _safe_name(workspace.key)

    def _refresh_due(self, path: Path) -> bool:
        if not (path / ".git").exists():
            return True
        stamp = path / ".codex-bg-refresh"
        if not stamp.exists():
            return True
        try:
            last_refresh = float(stamp.read_text(encoding="utf-8").strip())
        except ValueError:
            return True
        return time.time() - last_refresh >= self.app.workspace_refresh_interval_seconds

    def _write_refresh_stamp(self, path: Path) -> None:
        (path / ".codex-bg-refresh").write_text(str(time.time()), encoding="utf-8")

    def _ensure_clean(self, path: Path, key: str) -> None:
        result = self.runner.run(["git", "status", "--porcelain"], cwd=path)
        if result.stdout.strip():
            raise WorkspaceError(
                f"shared workspace {key} is dirty; shared repo checkouts are read-only "
                "and changes must be made in separate worktrees"
            )


def subject_artifact_dir(root: Path, task: Task) -> Path:
    return root / "subjects" / _safe_name(task.plugin_name) / _safe_name(task.subject_id)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", value).strip("_") or "default"
