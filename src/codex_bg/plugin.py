from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from codex_bg.config import AppConfig, PluginConfig
from codex_bg.models import AiResult, Event, Task
from codex_bg.runner import Runner


@dataclass(frozen=True)
class PluginContext:
    app: AppConfig
    plugin: PluginConfig
    runner: Runner
    debug: Callable[[str], None] = lambda message: None


class SchedulerPlugin(Protocol):
    name: str

    def generate_events(self, context: PluginContext) -> list[Event]:
        ...

    def handle_result(self, context: PluginContext, task: Task, result: AiResult) -> None:
        ...

    def cleanup(self, context: PluginContext, subject_id: str) -> None:
        ...
