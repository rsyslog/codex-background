from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_bg.models import AiResult, Event, TaskStatus
from codex_bg.store import Store


class StoreTests(unittest.TestCase):
    def test_enqueue_dedupes_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")
            event = Event(
                plugin_name="p",
                event_type="triage",
                external_id="1",
                subject_id="repo#1",
                prompt="triage",
            )

            self.assertTrue(store.enqueue_event(event))
            self.assertFalse(store.enqueue_event(event))

    def test_callback_failed_reuses_latest_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")
            store.enqueue_event(
                Event(
                    plugin_name="p",
                    event_type="triage",
                    external_id="1",
                    subject_id="repo#1",
                    prompt="triage",
                )
            )
            task = store.lease_next_task("worker")
            assert task is not None
            result = AiResult(
                task_id=task.id,
                status="complete",
                final_message="done",
                structured={"comment": "done"},
                codex_session_id="session-1",
                artifact_dir=str(Path(tmp) / "artifacts"),
            )
            store.record_run(result)
            store.mark_callback_failed(task.id, "boom")

            leased = store.lease_next_task("worker")
            assert leased is not None
            self.assertEqual(leased.status, TaskStatus.CALLBACK_FAILED)
            self.assertEqual(store.latest_result(leased.id), result)


if __name__ == "__main__":
    unittest.main()
