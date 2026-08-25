"""Resource lifecycle tests for the SQLite TaskStore.

These tests verify that connections are closed deterministically so that:
- temporary SQLite files can be deleted immediately after use;
- no sqlite3 ResourceWarning escapes the store;
- the public _connect() contract (returning a usable connection) is preserved.
"""
import os
import sqlite3
import tempfile
import unittest
import warnings

from evoagent.store import TaskStore


class StoreConnectionLifecycleTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_connection_is_closed_so_file_is_deletable(self):
        # Any leaked handle would make os.unlink fail on Windows.
        self.store.create("t0", "repo/a", None, {"source": "test"})
        self.store.get("t0")
        self.store.list_tasks()
        os.unlink(self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_no_resource_warning_emitted(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.store.create("t-res", "repo/b", 1, {"source": "test"})
            self.store.succeed(
                "t-res", self._report(), self._event("SUCCESS")
            )
            self.store.get("t-res")
        resource_warnings = [
            item for item in caught
            if issubclass(item.category, ResourceWarning)
        ]
        self.assertEqual(
            [], resource_warnings,
            "store operations leaked sqlite ResourceWarnings: %r" % resource_warnings,
        )

    def test_connect_returns_usable_connection(self):
        # The public _connect() contract must be preserved.
        conn = self.store._connect()
        try:
            self.assertIsInstance(conn, sqlite3.Connection)
            row = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_rollback_on_error_keeps_store_usable(self):
        self.store.create("t-rollback", "repo/c", None, {"source": "test"})
        # A duplicate primary key forces a rollback inside the store.
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create("t-rollback", "repo/c", None, {"source": "test"})
        # The store remains usable afterwards.
        self.assertEqual("PENDING", self.store.get("t-rollback")["state"])

    @staticmethod
    def _report():
        from evoagent.models import ReviewReport
        return ReviewReport(
            repository="repo/b", pull_request=1, summary="s", risk="low",
        )

    @staticmethod
    def _event(_state):
        from evoagent.models import TaskState, TraceEvent
        from evoagent.store import utc_now
        return TraceEvent(1, TaskState.SUCCESS, "done", utc_now())


if __name__ == "__main__":
    unittest.main()