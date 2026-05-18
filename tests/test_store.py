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

    def test_enqueue_with_limits_drops_events_over_hourly_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")

            first = store.enqueue_event_with_limits(_event("1"), 1, None)
            second = store.enqueue_event_with_limits(_event("2"), 1, None)

            self.assertTrue(first.accepted)
            self.assertEqual(second.status, "rate_limited_hour")

    def test_enqueue_with_limits_drops_events_over_daily_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")

            first = store.enqueue_event_with_limits(_event("1"), None, 1)
            second = store.enqueue_event_with_limits(_event("2"), None, 1)

            self.assertTrue(first.accepted)
            self.assertEqual(second.status, "rate_limited_day")

    def test_duplicate_events_do_not_consume_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")
            first = _event("1")

            self.assertTrue(store.enqueue_event_with_limits(first, 2, None).accepted)
            self.assertEqual(store.enqueue_event_with_limits(first, 2, None).status, "duplicate")
            self.assertTrue(store.enqueue_event_with_limits(_event("2"), 2, None).accepted)
            self.assertEqual(
                store.enqueue_event_with_limits(_event("3"), 2, None).status,
                "rate_limited_hour",
            )

    def test_rate_limit_ignores_events_outside_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")
            store.conn.execute(
                "INSERT INTO plugin_rate_events (plugin_name, created_at) VALUES (?, ?)",
                ("p", "2000-01-01T00:00:00+00:00"),
            )
            store.conn.commit()

            result = store.enqueue_event_with_limits(_event("1"), 1, 1)

            self.assertTrue(result.accepted)

    def test_rate_limit_purge_removes_old_events_for_removed_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")
            store.conn.execute(
                "INSERT INTO plugin_rate_events (plugin_name, created_at) VALUES (?, ?)",
                ("removed", "2000-01-01T00:00:00+00:00"),
            )
            store.conn.commit()

            store.enqueue_event_with_limits(_event("1"), None, None)

            row = store.conn.execute(
                "SELECT COUNT(*) AS count FROM plugin_rate_events WHERE plugin_name = ?",
                ("removed",),
            ).fetchone()
            self.assertEqual(int(row["count"]), 0)

    def test_has_pending_work_tracks_queued_and_leased_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")

            self.assertFalse(store.has_pending_work())
            store.enqueue_event(
                Event(
                    plugin_name="p",
                    event_type="triage",
                    external_id="1",
                    subject_id="repo#1",
                    prompt="triage",
                )
            )
            self.assertTrue(store.has_pending_work())

            task = store.lease_next_task("worker")
            assert task is not None
            self.assertFalse(store.has_pending_work())

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

    def test_plugin_run_state_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")

            self.assertIsNone(store.plugin_last_run("plugin"))
            first = store.mark_plugin_run("plugin")
            second = store.plugin_last_run("plugin")

            self.assertEqual(first, second)

    def test_plugin_state_round_trips_json_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.sqlite3")

            self.assertEqual(
                store.get_plugin_state("plugin", "key", {"missing": True}),
                {"missing": True},
            )
            store.set_plugin_state("plugin", "key", {"last_seen": "2026-05-18T00:00:00Z"})

            self.assertEqual(
                store.get_plugin_state("plugin", "key"),
                {"last_seen": "2026-05-18T00:00:00Z"},
            )

def _event(external_id: str) -> Event:
    return Event(
        plugin_name="p",
        event_type="triage",
        external_id=external_id,
        subject_id=f"repo#{external_id}",
        prompt="triage",
    )


if __name__ == "__main__":
    unittest.main()
