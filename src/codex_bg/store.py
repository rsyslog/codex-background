from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sqlite3

from codex_bg.models import AiResult, Event, Task, TaskStatus, utcnow


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
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
        dedupe_key = event.dedupe_key or _dedupe_key(event)
        now = utcnow()
        try:
            self.conn.execute(
                """
                INSERT INTO tasks (
                    plugin_name, event_type, external_id, subject_id, prompt,
                    payload_json, executor_options_json, workspace_key, dedupe_key, priority, status,
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
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def lease_next_task(self, lease_owner: str) -> Task | None:
        now = utcnow()
        with self.conn:
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

    def get_task(self, task_id: int) -> Task:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        return _task_from_row(row)

    def mark_running(self, task_id: int) -> None:
        self._set_status(task_id, TaskStatus.RUNNING)

    def mark_complete(self, task_id: int, session_id: str | None = None) -> None:
        now = utcnow()
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
        row = self.conn.execute(
            "SELECT last_run_at FROM plugin_runs WHERE plugin_name = ?",
            (plugin_name,),
        ).fetchone()
        return row["last_run_at"] if row else None

    def mark_plugin_run(self, plugin_name: str) -> str:
        now = utcnow()
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

    def _set_status(self, task_id: int, status: TaskStatus) -> None:
        self.conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, utcnow(), task_id),
        )
        self.conn.commit()


def _dedupe_key(event: Event) -> str:
    return f"{event.plugin_name}:{event.event_type}:{event.external_id}"


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
