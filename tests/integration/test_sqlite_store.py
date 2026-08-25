"""SQLite Durable Control Plane integration tests (hardening plan section 5.2).

Exercises the :class:`SQLiteControlPlaneStore` directly: put/get/list/delete,
restart durability, WAL and atomic transactional Promote/Rollback batches.
"""
import os
import tempfile
import unittest

import pytest

from evoagent.storage.control_plane import SQLiteControlPlaneStore

pytestmark = pytest.mark.sqlite


class TestSQLiteControlPlaneStore(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".control.sqlite")
        os.close(handle)
        self.store = SQLiteControlPlaneStore(self.path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    def test_put_get_list_delete(self):
        self.store.put("policy_deployments", "dep-1", {"state": "CANARY", "v": 2})
        self.store.put("policy_deployments", "dep-2", {"state": "PROMOTED", "v": 3})
        self.assertEqual(self.store.get("policy_deployments", "dep-1")["state"], "CANARY")
        self.assertEqual(len(self.store.list("policy_deployments")), 2)
        self.assertTrue(self.store.delete("policy_deployments", "dep-1"))
        self.assertFalse(self.store.delete("policy_deployments", "dep-1"))
        self.assertIsNone(self.store.get("policy_deployments", "dep-1"))

    def test_legacy_repository_surface(self):
        # Repositories use save/all/count/delete/save_many/get_many/tables.
        self.store.save("runtime_policy_versions", "baseline-high", {"content": {"a": 1}})
        self.store.save_many("evolution_lineage", {"l1": {"parent": None}, "l2": {}})
        self.assertEqual(self.store.count("runtime_policy_versions"), 1)
        self.assertEqual(self.store.count("evolution_lineage"), 2)
        self.assertEqual(self.store.get_many("evolution_lineage")["l1"], {"parent": None})
        self.assertIn("runtime_policy_versions", self.store.tables())
        self.assertIn("evolution_lineage", self.store.tables())
        self.assertEqual(self.store.all("runtime_policy_versions")[0]["content"], {"a": 1})

    def test_durability_across_restart(self):
        self.store.put("policy_deployments", "dep-x", {"state": "PROPOSED"})
        reopened = SQLiteControlPlaneStore(self.path)  # simulated service restart
        self.assertEqual(
            reopened.get("policy_deployments", "dep-x")["state"], "PROPOSED")
        self.assertEqual(
            self.store.count("policy_deployments"),
            reopened.count("policy_deployments"))

    def test_wal_journal_mode(self):
        conn = self.store._connect()
        try:
            row = conn.execute("PRAGMA journal_mode").fetchone()
        finally:
            conn.close()
        # WAL may be reported upper/lower case across sqlite builds.
        self.assertEqual(str(row[0]).upper(), "WAL")

    def test_transaction_atomic_promote(self):
        # Promote updates two collections; a raise mid-batch rolls both back.
        with self.store.transaction() as atomic:
            atomic.put("policy_deployments", "dep", {"state": "PROMOTED"})
            atomic.put("runtime_policy_versions", "active", {"policy_id": "p1", "status": "ACTIVE"})

        self.assertEqual(self.store.get("policy_deployments", "dep")["state"], "PROMOTED")

        with self.store.transaction() as atomic:
            atomic.put("policy_deployments", "dep", {"state": "CANDIDATE"})
            atomic.delete("runtime_policy_versions", "active")

        self.assertIsNone(self.store.get("runtime_policy_versions", "active"))
        self.assertEqual(self.store.get("policy_deployments", "dep")["state"], "CANDIDATE")

        # A failing transaction must roll everything back.
        class Boom(Exception):
            pass

        try:
            with self.store.transaction() as atomic:
                atomic.put("policy_deployments", "dep", {"state": "ROLLED_BACK"})
                atomic.put("runtime_policy_versions", "active", {"status": "ACTIVE"})
                raise Boom()
        except Boom:
            pass
        # Neither update persisted.
        self.assertEqual(self.store.get("policy_deployments", "dep")["state"], "CANDIDATE")
        self.assertIsNone(self.store.get("runtime_policy_versions", "active"))


if __name__ == "__main__":
    unittest.main()