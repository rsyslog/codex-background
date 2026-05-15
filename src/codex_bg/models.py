from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class TaskStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    BLOCKED = "blocked"
    CALLBACK_FAILED = "callback_failed"


@dataclass(frozen=True)
class Event:
    plugin_name: str
    event_type: str
    external_id: str
    subject_id: str
    prompt: str
    payload: dict[str, Any] = field(default_factory=dict)
    workspace_key: str | None = None
    dedupe_key: str | None = None
    priority: int = 100
    executor_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    id: int
    plugin_name: str
    event_type: str
    external_id: str
    subject_id: str
    prompt: str
    payload: dict[str, Any]
    workspace_key: str | None
    dedupe_key: str
    priority: int
    status: TaskStatus
    attempts: int
    codex_session_id: str | None
    executor_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AiResult:
    task_id: int
    status: str
    final_message: str
    structured: dict[str, Any]
    codex_session_id: str | None
    artifact_dir: str
    error: str | None = None
