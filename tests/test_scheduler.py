from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from codex_bg.config import AppConfig, CodexConfig, PluginConfig
from codex_bg.models import AiResult, Event, Task
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

    def generate_events(self, context: PluginContext):
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
            self.assertEqual(plugin.handled[0][1].structured, {"comment": "ok"})


if __name__ == "__main__":
    unittest.main()
