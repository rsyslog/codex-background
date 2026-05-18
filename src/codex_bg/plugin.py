from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from codex_bg.config import AppConfig, PluginConfig
from codex_bg.models import AiResult, Event, Task
from codex_bg.notify import Notification, NotificationSeverity, Notifier
from codex_bg.runner import Runner


@dataclass(frozen=True)
class PluginContext:
    app: AppConfig
    plugin: PluginConfig
    runner: Runner
    debug: Callable[[str], None] = lambda message: None
    notifier: Notifier | None = None

    def notify(
        self,
        severity: NotificationSeverity,
        message: str,
        *,
        subject_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Send an operator-facing notification.

        Plugins should use this for user-visible events that may later route to
        Telegram or another notifier. Debugging details should stay in debug().
        """
        if self.notifier is None:
            return
        self.notifier.notify(
            Notification(
                severity=severity,
                message=message,
                subject_id=subject_id,
                details=details,
            )
        )


class SchedulerPlugin(Protocol):
    name: str
    default_rate_limit_per_hour: int | None
    default_rate_limit_per_day: int | None

    def generate_events(self, context: PluginContext) -> list[Event]:
        """Return currently available events for scheduled/plugin-polled sources."""
        ...

    def handle_result(self, context: PluginContext, task: Task, result: AiResult) -> None: ...

    def cleanup(self, context: PluginContext, subject_id: str) -> None: ...


class EventSink(Protocol):
    """Notification interface used by plugin-owned listener sources.

    Long-running plugins can block on external input and call submit() as soon
    as work arrives. The scheduler persists those events and wakes the worker.
    """

    def submit(self, events: Iterable[Event]) -> int: ...
