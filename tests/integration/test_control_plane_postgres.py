"""PostgreSQL Durable Control Plane integration tests (hardening plan section 5.3).

Requires a live PostgreSQL 16 (``EVOREVIEW_DB_DSN`` or ``EVOAGENT_DATABASE_URL``).
Verified in CI via the ``postgres-integration`` job; skipped otherwise so local
runs stay zero-config.
"""
import os
import unittest

import pytest

pytestmark = pytest.mark.postgres


def _dsn() -> str:
    return (
        os.environ.get("EVOREVIEW_DB_DSN")
        or os.environ.get("EVOAGENT_DATABASE_URL")
        or ""
    )


@pytest.mark.skipif(not _dsn(), reason="requires EVOREVIEW_DB_DSN postgres DSN")
class TestPostgresControlPlaneStore(unittest.TestCase):
    def setUp(self):
        from evoagent.storage.control_plane import PostgresControlPlaneStore
        self.store = PostgresControlPlaneStore(_dsn())
        # isolate this test's rows
        self.prefix = "postgrescp_test_"
        self._cleanup()

    def _cleanup(self):
        with self.store._connection() as conn:
            conn.execute(
                "DELETE FROM control_records WHERE collection LIKE %s",
                (self.prefix + "%",),
            )

    def tearDown(self):
        self._cleanup()

    def test_put_get_restart(self):
        coll = self.prefix + "policy_deployments"
        self.store.put(coll, "dep-1", {"state": "PROMOTED", "metrics": [1, 2, 3]})
        # "restart": a fresh handle over the same database reads the row back.
        from evoagent.storage.control_plane import PostgresControlPlaneStore
        reopened = PostgresControlPlaneStore(_dsn())
        self.assertEqual(reopened.get(coll, "dep-1")["state"], "PROMOTED")
        self.assertTrue(reopened.delete(coll, "dep-1"))
        self.assertIsNone(reopened.get(coll, "dep-1"))

    def test_legacy_repository_surface(self):
        coll = self.prefix + "runtime_policy_versions"
        self.store.save(coll, "baseline-high", {"content": {"x": 1}})
        self.store.save_many(coll, {"a": {"v": 1}, "b": {"v": 2}})
        self.assertEqual(self.store.count(coll), 3)
        self.assertEqual(self.store.get_many(coll)["a"], {"v": 1})
        self.assertEqual(len(self.store.list(coll)), 3)
        self.assertTrue(self.store.delete(coll, "a"))

    def test_transaction_rolls_back(self):
        coll = self.prefix + "deployments"
        self.store.put(coll, "dep", {"state": "CANDIDATE"})

        class Boom(Exception):
            pass

        try:
            with self.store.transaction() as atomic:
                atomic.put(coll, "dep", {"state": "PROMOTED"})
                atomic.put(self.prefix + "lineage", "l1", {"parent": None})
                raise Boom()
        except Boom:
            pass
        # rollback kept nothing: the promote never landed and no lineage row exists.
        self.assertEqual(self.store.get(coll, "dep")["state"], "CANDIDATE")
        self.assertIsNone(self.store.get(self.prefix + "lineage", "l1"))


if __name__ == "__main__":
    unittest.main()