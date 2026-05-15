from __future__ import annotations

import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from typing import Any

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
        self.debug(
            f"loading plugins: {', '.join(plugin.name for plugin in app.plugins) or '(none)'}"
        )
        self.plugins = load_plugins(app)
        self.lease_owner = f"worker-{uuid.uuid4()}"
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self.debug(f"scheduler initialized with database {app.database_path}")
        self.debug(f"shared workspace root is {app.workspace_root}")
        if app.dry_run:
            self.debug("dry-run mode enabled; plugin callbacks must not mutate external systems")

    def run_forever(self) -> None:
        threads = self._start_event_sources()
        try:
            self._worker_loop()
        except KeyboardInterrupt:
            self.debug("shutdown requested")
        finally:
            self._stop_event.set()
            with self._condition:
                self._condition.notify_all()
            for thread in threads:
                thread.join(timeout=5)

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
            count += self._submit_events(loaded.instance.generate_events(context))
            last_run = self.store.mark_plugin_run(loaded.config.name)
            self.debug(f"plugin {loaded.config.name} run recorded at {last_run}")
        return count

    def work_one(self) -> bool:
        task = self.store.lease_next_task(self.lease_owner)
        if task is None:
            self.debug("no queued task available")
            return False

        self.debug(
            f"leased task {task.id} {task.plugin_name}/{task.event_type} for {task.subject_id}"
        )
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

    def _submit_events(self, events: Iterable[Any]) -> int:
        count = 0
        for event in events:
            if self.store.enqueue_event(event):
                self.debug(
                    f"enqueued {event.event_type} from {event.plugin_name} for {event.subject_id}"
                )
                count += 1
            else:
                self.debug(f"skipped duplicate event for {event.subject_id}")
        if count:
            # Producers notify the worker immediately. This is the core event
            # handoff: the worker does not need to poll to discover new tasks.
            with self._condition:
                self._condition.notify_all()
        return count

    def _start_event_sources(self) -> list[threading.Thread]:
        threads = [
            threading.Thread(
                target=self._workspace_refresh_loop,
                name="workspace-refresh",
            )
        ]
        for loaded in self.plugins.values():
            threads.append(
                threading.Thread(
                    target=self._plugin_source_loop,
                    name=f"plugin-source-{loaded.config.name}",
                    args=(loaded,),
                )
            )
        for thread in threads:
            thread.start()
        return threads

    def _workspace_refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                refreshed = self.refresh_workspaces()
                if refreshed:
                    self.debug(f"workspace refresh source refreshed {refreshed} workspace(s)")
            except Exception as exc:
                self.debug(f"workspace refresh source failed: {exc}")
            self._stop_event.wait(max(1, self.app.workspace_refresh_interval_seconds))

    def _plugin_source_loop(self, loaded: LoadedPlugin) -> None:
        listener = getattr(loaded.instance, "run_event_source", None)
        if listener:
            self._run_plugin_listener(loaded, listener)
            return
        self._run_scheduled_plugin_source(loaded)

    def _run_plugin_listener(self, loaded: LoadedPlugin, listener: Any) -> None:
        context = PluginContext(self.app, loaded.config, self.runner, self.debug)
        sink = _SchedulerEventSink(self)
        self.debug(f"starting listener source for plugin {loaded.config.name}")
        # Listener plugins own their blocking wait, e.g. an HTTP webhook server,
        # message queue consumer, or filesystem watcher. They submit events via
        # the sink as soon as external activity arrives.
        listener(context, sink, self._stop_event)

    def _run_scheduled_plugin_source(self, loaded: LoadedPlugin) -> None:
        while not self._stop_event.is_set():
            if self._plugin_due(loaded.config):
                self._run_scheduled_plugin_once(loaded)
            self._stop_event.wait(self._plugin_sleep_seconds(loaded.config))

    def _run_scheduled_plugin_once(self, loaded: LoadedPlugin) -> None:
        try:
            self.debug(f"scheduled source running plugin {loaded.config.name}")
            last_run = self.store.mark_plugin_run(loaded.config.name)
            self.debug(f"plugin {loaded.config.name} run attempt recorded at {last_run}")
            context = PluginContext(self.app, loaded.config, self.runner, self.debug)
            self._submit_events(loaded.instance.generate_events(context))
        except Exception as exc:
            self.debug(f"plugin source {loaded.config.name} failed: {exc}")

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            while self.work_one():
                pass
            self._wait_for_work_notification()

    def _wait_for_work_notification(self) -> None:
        with self._condition:
            if self._stop_event.is_set():
                return
            if self.store.has_pending_work():
                self.debug("worker found queued work before waiting")
                return
            self.debug("worker waiting for event notification")
            self._condition.wait(timeout=60)

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

    def _plugin_sleep_seconds(self, plugin_config: PluginConfig) -> int:
        interval = self._plugin_interval(plugin_config)
        if interval <= 0:
            return max(1, self.app.poll_interval_seconds)
        last_run = self.store.plugin_last_run(plugin_config.name)
        if last_run is None:
            return 1
        remaining = interval - _age_seconds(last_run)
        return max(1, int(remaining))


def load_plugins(app: AppConfig) -> dict[str, LoadedPlugin]:
    loaded: dict[str, LoadedPlugin] = {}
    for plugin_config in app.plugins:
        module = import_module(plugin_config.module)
        factory = getattr(module, "create_plugin", None)
        plugin = factory(plugin_config) if factory else module.Plugin()
        loaded[plugin_config.name] = LoadedPlugin(plugin_config, plugin)
    return loaded


def _age_seconds(timestamp: str) -> float:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - parsed).total_seconds()


class _SchedulerEventSink:
    def __init__(self, scheduler: Scheduler):
        self.scheduler = scheduler

    def submit(self, events: Iterable[Any]) -> int:
        return self.scheduler._submit_events(events)
