from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

NotificationSeverity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class Notification:
    severity: NotificationSeverity
    message: str
    subject_id: str | None = None
    details: dict[str, Any] | None = None


class Notifier(Protocol):
    """Extension point for operator-facing notifications.

    The scheduler uses this API for events that should be visible outside debug
    logs, such as rate-limit drops or failed work. Future integrations like
    Telegram should implement this protocol instead of scraping stdout.
    """

    def notify(self, notification: Notification) -> None:
        raise NotImplementedError


class StdoutNotifier:
    """Initial notifier implementation.

    It intentionally prints even when --debug is disabled because notifications
    are operator signals, not diagnostic logs.
    """

    def notify(self, notification: Notification) -> None:
        subject = f" subject={notification.subject_id}" if notification.subject_id else ""
        print(f"[codex-bg:{notification.severity}]{subject} {notification.message}", flush=True)
