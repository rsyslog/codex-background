from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from codex_bg.config import AppConfig, CodexConfig, PluginConfig
from codex_bg.models import AiResult, Event, Task
from codex_bg.notify import Notification
from codex_bg.plugin import PluginContext
from codex_bg.runner import CommandResult
from codex_bg.scheduler import Scheduler
from codex_bg.store import Store


class FakeRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def run(self, args, *, cwd=None, input_text=None, check=True):
        self.calls.append(list(args))
        if args[0] == "codex" and "exec" in args:
            out_file = args[args.index("-o") + 1]
            Path(out_file).write_text('{"comment":"ok"}', encoding="utf-8")
            return CommandResult(list(args), 0, '{"session_id":"session-1"}\n', "")
        return CommandResult(list(args), 0, "", "")


class MemoryPlugin:
    name = "memory"

    def __init__(self):
        self.handled: list[tuple[Task, AiResult]] = []
        self.generate_calls = 0

    def generate_events(self, context: PluginContext):
        self.generate_calls += 1
        return [
            Event(
                plugin_name="memory",
                event_type="test",
                external_id="1",
                subject_id="subject",
                prompt="Return JSON.",
            )
        ]

    def handle_result(self, context: PluginContext, task: Task, result: AiResult) -> None:
        self.handled.append((task, result))

    def cleanup(self, context: PluginContext, subject_id: str) -> None:
        return None


class MultiEventPlugin:
    name = "multi"
    default_rate_limit_per_hour = 1
    default_rate_limit_per_day = None

    def __init__(self):
        self.generate_calls = 0

    def generate_events(self, context: PluginContext):
        self.generate_calls += 1
        return [
            Event(
                plugin_name="multi",
                event_type="test",
                external_id="1",
                subject_id="subject-1",
                prompt="Return JSON.",
            ),
            Event(
                plugin_name="multi",
                event_type="test",
                external_id="2",
                subject_id="subject-2",
                prompt="Return JSON.",
            ),
        ]

    def handle_result(self, context: PluginContext, task: Task, result: AiResult) -> None:
        return None

    def cleanup(self, context: PluginContext, subject_id: str) -> None:
        return None


class FailingPlugin:
    name = "failing"

    def generate_events(self, context: PluginContext):
        raise RuntimeError("poll failed")

    def handle_result(self, context: PluginContext, task: Task, result: AiResult) -> None:
        return None

    def cleanup(self, context: PluginContext, subject_id: str) -> None:
        return None


class FakeNotifier:
    def __init__(self):
        self.notifications: list[Notification] = []

    def notify(self, notification: Notification) -> None:
        self.notifications.append(notification)


class SchedulerTests(unittest.TestCase):
    def test_once_generates_runs_and_callbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = MemoryPlugin()
            module = types.ModuleType("test_memory_plugin")
            module.create_plugin = lambda config: plugin
            import sys

            sys.modules["test_memory_plugin"] = module
            app = AppConfig(
                database_path=Path(tmp) / "state.sqlite3",
                workdir_root=Path(tmp) / "workdirs",
                codex=CodexConfig(),
                plugins=[PluginConfig(name="memory", module="test_memory_plugin")],
            )
            scheduler = Scheduler(app, store=Store(app.database_path), runner=FakeRunner())  # type: ignore[arg-type]

            first = scheduler.once()
            second = scheduler.once()

            self.assertEqual(first, {"refreshed": 0, "generated": 1, "worked": True})
            self.assertEqual(second, {"refreshed": 0, "generated": 0, "worked": False})
            self.assertEqual(len(plugin.handled), 1)
            self.assertEqual(plugin.generate_calls, 1)
            self.assertEqual(plugin.handled[0][1].structured, {"comment": "ok"})

    def test_zero_interval_plugin_runs_every_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = MemoryPlugin()
            module = types.ModuleType("test_memory_plugin_always")
            module.create_plugin = lambda config: plugin
            import sys

            sys.modules["test_memory_plugin_always"] = module
            app = AppConfig(
                database_path=Path(tmp) / "state.sqlite3",
                workdir_root=Path(tmp) / "workdirs",
                codex=CodexConfig(),
                plugins=[
                    PluginConfig(
                        name="memory",
                        module="test_memory_plugin_always",
                        interval_seconds=0,
                    )
                ],
            )
            scheduler = Scheduler(app, store=Store(app.database_path), runner=FakeRunner())  # type: ignore[arg-type]

            scheduler.once()
            scheduler.once()

            self.assertEqual(plugin.generate_calls, 2)

    def test_failed_scheduled_plugin_run_records_attempt_for_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = FailingPlugin()
            module = types.ModuleType("test_failing_plugin")
            module.create_plugin = lambda config: plugin
            import sys

            sys.modules["test_failing_plugin"] = module
            config = PluginConfig(
                name="failing",
                module="test_failing_plugin",
                interval_seconds=900,
            )
            app = AppConfig(
                database_path=Path(tmp) / "state.sqlite3",
                workdir_root=Path(tmp) / "workdirs",
                plugins=[config],
            )
            scheduler = Scheduler(app, store=Store(app.database_path), runner=FakeRunner())  # type: ignore[arg-type]
            loaded = scheduler.plugins["failing"]

            scheduler._run_scheduled_plugin_once(loaded)

            self.assertIsNotNone(scheduler.store.plugin_last_run("failing"))
            self.assertFalse(scheduler._plugin_due(config))

    def test_worker_wait_rechecks_durable_queue_before_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = MemoryPlugin()
            module = types.ModuleType("test_memory_plugin_wait")
            module.create_plugin = lambda config: plugin
            import sys

            sys.modules["test_memory_plugin_wait"] = module
            app = AppConfig(
                database_path=Path(tmp) / "state.sqlite3",
                workdir_root=Path(tmp) / "workdirs",
                plugins=[PluginConfig(name="memory", module="test_memory_plugin_wait")],
            )
            scheduler = Scheduler(app, store=Store(app.database_path), runner=FakeRunner())  # type: ignore[arg-type]
            scheduler.store.enqueue_event(
                Event(
                    plugin_name="memory",
                    event_type="test",
                    external_id="1",
                    subject_id="subject",
                    prompt="Return JSON.",
                )
            )

            scheduler._wait_for_work_notification()

            self.assertTrue(scheduler.store.has_pending_work())

    def test_submit_events_applies_plugin_default_rate_limit_and_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = MultiEventPlugin()
            module = types.ModuleType("test_multi_plugin_defaults")
            module.create_plugin = lambda config: plugin
            import sys

            sys.modules["test_multi_plugin_defaults"] = module
            notifier = FakeNotifier()
            app = AppConfig(
                database_path=Path(tmp) / "state.sqlite3",
                workdir_root=Path(tmp) / "workdirs",
                plugins=[PluginConfig(name="multi", module="test_multi_plugin_defaults")],
            )
            scheduler = Scheduler(
                app,
                store=Store(app.database_path),
                runner=FakeRunner(),  # type: ignore[arg-type]
                notifier=notifier,
            )

            generated = scheduler.generate_events()

            self.assertEqual(generated, 1)
            self.assertTrue(scheduler.store.has_pending_work())
            self.assertEqual(len(notifier.notifications), 1)
            self.assertEqual(notifier.notifications[0].severity, "warning")
            self.assertEqual(notifier.notifications[0].subject_id, "multi")
            self.assertIn("dropped 1 event", notifier.notifications[0].message)

    def test_configured_rate_limit_overrides_plugin_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = MultiEventPlugin()
            module = types.ModuleType("test_multi_plugin_config_override")
            module.create_plugin = lambda config: plugin
            import sys

            sys.modules["test_multi_plugin_config_override"] = module
            app = AppConfig(
                database_path=Path(tmp) / "state.sqlite3",
                workdir_root=Path(tmp) / "workdirs",
                plugins=[
                    PluginConfig(
                        name="multi",
                        module="test_multi_plugin_config_override",
                        rate_limit_per_hour=2,
                    )
                ],
            )
            scheduler = Scheduler(app, store=Store(app.database_path), runner=FakeRunner())  # type: ignore[arg-type]

            generated = scheduler.generate_events()

            self.assertEqual(generated, 2)

    def test_status_reports_effective_rate_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = MultiEventPlugin()
            module = types.ModuleType("test_multi_plugin_status")
            module.create_plugin = lambda config: plugin
            import sys

            sys.modules["test_multi_plugin_status"] = module
            app = AppConfig(
                database_path=Path(tmp) / "state.sqlite3",
                workdir_root=Path(tmp) / "workdirs",
                plugins=[PluginConfig(name="multi", module="test_multi_plugin_status")],
            )
            scheduler = Scheduler(app, store=Store(app.database_path), runner=FakeRunner())  # type: ignore[arg-type]

            status = scheduler.status()

            self.assertEqual(status["plugins"][0]["rate_limit_per_hour"], 1)
            self.assertIsNone(status["plugins"][0]["rate_limit_per_day"])


if __name__ == "__main__":
    unittest.main()
