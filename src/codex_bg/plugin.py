from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

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
        """Return currently available events for scheduled/plugin-polled sources."""
        ...

    def handle_result(self, context: PluginContext, task: Task, result: AiResult) -> None:
        ...

    def cleanup(self, context: PluginContext, subject_id: str) -> None:
        ...


class EventSink(Protocol):
    """Notification interface used by plugin-owned listener sources.

    Long-running plugins can block on external input and call submit() as soon
    as work arrives. The scheduler persists those events and wakes the worker.
    """

    def submit(self, events: Iterable[Event]) -> int:
        ...
