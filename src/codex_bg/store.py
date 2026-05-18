from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from codex_bg.models import AiResult, Event, Task, TaskStatus, utcnow


@dataclass(frozen=True)
class EnqueueResult:
    status: str
    task_id: int | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def migrate(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                external_id TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                executor_options_json TEXT NOT NULL DEFAULT '{}',
                workspace_key TEXT,
                dedupe_key TEXT NOT NULL UNIQUE,
                priority INTEGER NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                codex_session_id TEXT,
                lease_owner TEXT,
                leased_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_status_priority
                ON tasks(status, priority, created_at);

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES tasks(id),
                status TEXT NOT NULL,
                artifact_dir TEXT NOT NULL,
                final_message TEXT NOT NULL,
                structured_json TEXT NOT NULL,
                codex_session_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plugin_runs (
                plugin_name TEXT PRIMARY KEY,
                last_run_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plugin_rate_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_plugin_rate_events_plugin_created
                ON plugin_rate_events(plugin_name, created_at);

            CREATE INDEX IF NOT EXISTS idx_plugin_rate_events_created
                ON plugin_rate_events(created_at);

            CREATE TABLE IF NOT EXISTS plugin_state (
                plugin_name TEXT NOT NULL,
                state_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (plugin_name, state_key)
            );
                """
            )
            self._ensure_column("tasks", "executor_options_json", "TEXT NOT NULL DEFAULT '{}'")
            self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def enqueue_event(self, event: Event) -> bool:
        return self.enqueue_event_with_limits(event, None, None).accepted

    def enqueue_event_with_limits(
        self,
        event: Event,
        rate_limit_per_hour: int | None,
        rate_limit_per_day: int | None,
    ) -> EnqueueResult:
        dedupe_key = event.dedupe_key or _dedupe_key(event)
        now = utcnow()
        with self._lock, self.conn:
            if self._dedupe_key_exists(dedupe_key):
                return EnqueueResult("duplicate")
            self._purge_old_rate_events(now)
            if self._rate_limit_exceeded(event.plugin_name, now, rate_limit_per_hour, 3600):
                return EnqueueResult("rate_limited_hour")
            if self._rate_limit_exceeded(event.plugin_name, now, rate_limit_per_day, 24 * 3600):
                return EnqueueResult("rate_limited_day")
            try:
                cursor = self.conn.execute(
                    """
                    INSERT INTO tasks (
                        plugin_name, event_type, external_id, subject_id, prompt,
                        payload_json, executor_options_json, workspace_key,
                        dedupe_key, priority, status,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.plugin_name,
                        event.event_type,
                        event.external_id,
                        event.subject_id,
                        event.prompt,
                        json.dumps(event.payload, sort_keys=True),
                        json.dumps(event.executor_options, sort_keys=True),
                        event.workspace_key,
                        dedupe_key,
                        event.priority,
                        TaskStatus.QUEUED.value,
                        now,
                        now,
                    ),
                )
                self.conn.execute(
                    """
                    INSERT INTO plugin_rate_events (plugin_name, created_at)
                    VALUES (?, ?)
                    """,
                    (event.plugin_name, now),
                )
                return EnqueueResult("accepted", cursor.lastrowid)
            except sqlite3.IntegrityError:
                return EnqueueResult("duplicate")

    def _dedupe_key_exists(self, dedupe_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM tasks WHERE dedupe_key = ? LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        return row is not None

    def _purge_old_rate_events(self, now: str) -> None:
        cutoff = _add_seconds(now, -(24 * 3600))
        self.conn.execute(
            """
            DELETE FROM plugin_rate_events
            WHERE created_at < ?
            """,
            (cutoff,),
        )

    def _rate_limit_exceeded(
        self,
        plugin_name: str,
        now: str,
        limit: int | None,
        window_seconds: int,
    ) -> bool:
        if limit is None:
            return False
        cutoff = _add_seconds(now, -window_seconds)
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count FROM plugin_rate_events
            WHERE plugin_name = ? AND created_at >= ?
            """,
            (plugin_name, cutoff),
        ).fetchone()
        return int(row["count"]) >= limit

    def lease_next_task(self, lease_owner: str) -> Task | None:
        now = utcnow()
        with self._lock, self.conn:
            row = self.conn.execute(
                """
                SELECT * FROM tasks
                WHERE status IN (?, ?)
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                (TaskStatus.QUEUED.value, TaskStatus.CALLBACK_FAILED.value),
            ).fetchone()
            if row is None:
                return None
            self.conn.execute(
                """
                UPDATE tasks
                SET status = ?, lease_owner = ?, leased_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (TaskStatus.LEASED.value, lease_owner, now, now, row["id"]),
            )
        return _task_from_row(row)

    def has_pending_work(self) -> bool:
        with self._lock:
            row = self.conn.execute(
                """
                SELECT 1 FROM tasks
                WHERE status IN (?, ?)
                LIMIT 1
                """,
                (TaskStatus.QUEUED.value, TaskStatus.CALLBACK_FAILED.value),
            ).fetchone()
        return row is not None

    def get_task(self, task_id: int) -> Task:
        with self._lock:
            row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        return _task_from_row(row)

    def mark_running(self, task_id: int) -> None:
        self._set_status(task_id, TaskStatus.RUNNING)

    def mark_complete(self, task_id: int, session_id: str | None = None) -> None:
        now = utcnow()
        with self._lock:
            self.conn.execute(
                """
                UPDATE tasks
                SET status = ?, codex_session_id = COALESCE(?, codex_session_id),
                    lease_owner = NULL, leased_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (TaskStatus.COMPLETE.value, session_id, now, task_id),
            )
            self.conn.commit()

    def mark_failed(self, task_id: int, error: str, status: TaskStatus = TaskStatus.FAILED) -> None:
        now = utcnow()
        with self._lock:
            self.conn.execute(
                """
                UPDATE tasks
                SET status = ?, attempts = attempts + 1, last_error = ?,
                    lease_owner = NULL, leased_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status.value, error, now, task_id),
            )
            self.conn.commit()

    def mark_callback_failed(self, task_id: int, error: str) -> None:
        self.mark_failed(task_id, error, TaskStatus.CALLBACK_FAILED)

    def record_run(self, result: AiResult) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO runs (
                    task_id, status, artifact_dir, final_message, structured_json,
                    codex_session_id, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.task_id,
                    result.status,
                    result.artifact_dir,
                    result.final_message,
                    json.dumps(result.structured, sort_keys=True),
                    result.codex_session_id,
                    result.error,
                    utcnow(),
                ),
            )
            self.conn.commit()

    def recent_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id, plugin_name, event_type, subject_id, status, attempts,
                       last_error, created_at, updated_at
                FROM tasks
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_result(self, task_id: int) -> AiResult | None:
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM runs
                WHERE task_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return AiResult(
            task_id=task_id,
            status=row["status"],
            final_message=row["final_message"],
            structured=json.loads(row["structured_json"]),
            codex_session_id=row["codex_session_id"],
            artifact_dir=row["artifact_dir"],
            error=row["error"],
        )

    def plugin_last_run(self, plugin_name: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT last_run_at FROM plugin_runs WHERE plugin_name = ?",
                (plugin_name,),
            ).fetchone()
        return row["last_run_at"] if row else None

    def mark_plugin_run(self, plugin_name: str) -> str:
        now = utcnow()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO plugin_runs (plugin_name, last_run_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(plugin_name) DO UPDATE SET
                    last_run_at = excluded.last_run_at,
                    updated_at = excluded.updated_at
                """,
                (plugin_name, now, now),
            )
            self.conn.commit()
        return now

    def get_plugin_state(self, plugin_name: str, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self.conn.execute(
                """
                SELECT value_json FROM plugin_state
                WHERE plugin_name = ? AND state_key = ?
                """,
                (plugin_name, key),
            ).fetchone()
        if row is None:
            return default
        return json.loads(row["value_json"])

    def set_plugin_state(self, plugin_name: str, key: str, value: Any) -> None:
        now = utcnow()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO plugin_state (plugin_name, state_key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(plugin_name, state_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (plugin_name, key, json.dumps(value, sort_keys=True), now),
            )
            self.conn.commit()

    def _set_status(self, task_id: int, status: TaskStatus) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, utcnow(), task_id),
            )
            self.conn.commit()


def _dedupe_key(event: Event) -> str:
    return f"{event.plugin_name}:{event.event_type}:{event.external_id}"


def _add_seconds(timestamp: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed + timedelta(seconds=seconds)).isoformat()


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        plugin_name=row["plugin_name"],
        event_type=row["event_type"],
        external_id=row["external_id"],
        subject_id=row["subject_id"],
        prompt=row["prompt"],
        payload=json.loads(row["payload_json"]),
        workspace_key=row["workspace_key"],
        dedupe_key=row["dedupe_key"],
        priority=row["priority"],
        status=TaskStatus(row["status"]),
        attempts=row["attempts"],
        codex_session_id=row["codex_session_id"],
        executor_options=json.loads(row["executor_options_json"]),
    )
