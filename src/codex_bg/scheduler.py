from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from time import sleep
from typing import Any
import uuid

from codex_bg.config import AppConfig, PluginConfig
from codex_bg.executor import CodexExecutor
from codex_bg.models import AiResult, Task, TaskStatus
from codex_bg.plugin import PluginContext, SchedulerPlugin
from codex_bg.runner import Runner
from codex_bg.store import Store
from codex_bg.workspace import WorkspaceError, WorkspaceManager


@dataclass(frozen=True)
class LoadedPlugin:
    config: PluginConfig
    instance: SchedulerPlugin


class Scheduler:
    def __init__(
        self,
        app: AppConfig,
        *,
        store: Store | None = None,
        runner: Runner | None = None,
    ):
        self.app = app
        self.store = store or Store(app.database_path)
        self.runner = runner or Runner()
        self.workspace_manager = WorkspaceManager(app, self.runner, debug=self.debug)
        self.executor = CodexExecutor(self.runner, app.workdir_root, app.codex, debug=self.debug)
        self.debug(f"loading plugins: {', '.join(plugin.name for plugin in app.plugins) or '(none)'}")
        self.plugins = load_plugins(app)
        self.lease_owner = f"worker-{uuid.uuid4()}"
        self.debug(f"scheduler initialized with database {app.database_path}")
        self.debug(f"shared workspace root is {app.workspace_root}")
        if app.dry_run:
            self.debug("dry-run mode enabled; plugin callbacks must not mutate external systems")

    def run_forever(self) -> None:
        while True:
            self.debug("starting scheduler cycle")
            self.once()
            self.debug(f"sleeping for {self.app.poll_interval_seconds}s")
            sleep(self.app.poll_interval_seconds)

    def once(self) -> dict[str, Any]:
        self.debug("running one scheduler cycle")
        refreshed = self.refresh_workspaces()
        generated = self.generate_events()
        worked = self.work_one()
        self.debug(f"cycle complete: refreshed={refreshed} generated={generated} worked={worked}")
        return {"refreshed": refreshed, "generated": generated, "worked": worked}

    def refresh_workspaces(self) -> int:
        self.debug("refreshing due shared workspaces")
        return self.workspace_manager.refresh_due_workspaces()

    def generate_events(self) -> int:
        count = 0
        for loaded in self.plugins.values():
            if not self._plugin_due(loaded.config):
                self.debug(f"plugin {loaded.config.name} is not due")
                continue
            self.debug(f"generating events with plugin {loaded.config.name}")
            context = PluginContext(self.app, loaded.config, self.runner, self.debug)
            for event in loaded.instance.generate_events(context):
                if self.store.enqueue_event(event):
                    self.debug(
                        f"enqueued {event.event_type} from {event.plugin_name} for {event.subject_id}"
                    )
                    count += 1
                else:
                    self.debug(f"skipped duplicate event for {event.subject_id}")
            last_run = self.store.mark_plugin_run(loaded.config.name)
            self.debug(f"plugin {loaded.config.name} run recorded at {last_run}")
        return count

    def work_one(self) -> bool:
        task = self.store.lease_next_task(self.lease_owner)
        if task is None:
            self.debug("no queued task available")
            return False

        self.debug(f"leased task {task.id} {task.plugin_name}/{task.event_type} for {task.subject_id}")
        plugin = self.plugins[task.plugin_name]
        context = PluginContext(self.app, plugin.config, self.runner, self.debug)
        if task.status == TaskStatus.CALLBACK_FAILED:
            self.debug(f"retrying callback for task {task.id}")
            result = self.store.latest_result(task.id)
            if result is None:
                self.store.mark_failed(task.id, "callback retry has no stored AI result")
                self.debug(f"task {task.id} failed: callback retry has no stored AI result")
                return True
            self._deliver_callback(context, plugin.instance, task, result)
            return True

        self.store.mark_running(task.id)
        try:
            cwd = self.workspace_manager.prepare(task)
            task = self.store.get_task(task.id)
            result = self.executor.run(task, cwd)
            self.store.record_run(result)
            if result.status != "complete":
                self.store.mark_failed(task.id, result.error or "codex execution failed")
                self.debug(f"task {task.id} failed during codex execution")
                return True
            self._deliver_callback(context, plugin.instance, task, result)
        except WorkspaceError as exc:
            self.store.mark_failed(task.id, str(exc), TaskStatus.BLOCKED)
            self.debug(f"task {task.id} blocked: {exc}")
        except Exception as exc:
            self.store.mark_failed(task.id, str(exc))
            self.debug(f"task {task.id} failed: {exc}")
        return True

    def status(self) -> dict[str, Any]:
        return {
            "plugins": [self._plugin_status(loaded.config) for loaded in self.plugins.values()],
            "tasks": self.store.recent_tasks(),
        }

    def _deliver_callback(
        self,
        context: PluginContext,
        plugin: SchedulerPlugin,
        task: Task,
        result: AiResult,
    ) -> None:
        try:
            self.debug(f"delivering callback for task {task.id}")
            plugin.handle_result(context, task, result)
        except Exception as exc:
            self.store.mark_callback_failed(task.id, str(exc))
            self.debug(f"callback failed for task {task.id}: {exc}")
            return
        self.store.mark_complete(task.id, result.codex_session_id)
        self.debug(f"task {task.id} complete")

    def debug(self, message: str) -> None:
        if self.app.debug:
            print(f"[codex-bg] {message}", flush=True)

    def _plugin_interval(self, plugin_config: PluginConfig) -> int:
        if plugin_config.interval_seconds is not None:
            return plugin_config.interval_seconds
        return self.app.poll_interval_seconds

    def _plugin_due(self, plugin_config: PluginConfig) -> bool:
        interval = self._plugin_interval(plugin_config)
        if interval <= 0:
            return True
        last_run = self.store.plugin_last_run(plugin_config.name)
        if last_run is None:
            return True
        return _age_seconds(last_run) >= interval

    def _plugin_status(self, plugin_config: PluginConfig) -> dict[str, Any]:
        interval = self._plugin_interval(plugin_config)
        last_run = self.store.plugin_last_run(plugin_config.name)
        return {
            "name": plugin_config.name,
            "module": plugin_config.module,
            "interval_seconds": interval,
            "last_run_at": last_run,
            "due": self._plugin_due(plugin_config),
        }


def load_plugins(app: AppConfig) -> dict[str, LoadedPlugin]:
    loaded: dict[str, LoadedPlugin] = {}
    for plugin_config in app.plugins:
        module = import_module(plugin_config.module)
        factory = getattr(module, "create_plugin", None)
        plugin = factory(plugin_config) if factory else getattr(module, "Plugin")()
        loaded[plugin_config.name] = LoadedPlugin(plugin_config, plugin)
    return loaded


def _age_seconds(timestamp: str) -> float:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - parsed).total_seconds()
