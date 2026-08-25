"""Closed-loop WP7: continuous learning, migration and forgetting monitoring."""
import os
import tempfile
import unittest

from evoagent import migration_monitor as monitor
from evoagent.store import TaskStore


class MigrationMatrixTests(unittest.TestCase):
    def test_matrix_grouping(self):
        records = [
            {"version": 1, "repository": "a/b", "language": "python", "metric": 0.8},
            {"version": 2, "repository": "a/b", "language": "python", "metric": 0.85},
            {"version": 1, "repository": "c/d", "language": "go", "metric": 0.7},
        ]
        matrix = monitor.migration_matrix(records)
        self.assertEqual({1: 0.8, 2: 0.85}, matrix[("a/b", "python")])
        self.assertEqual({1: 0.7}, matrix[("c/d", "go")])

    def test_marginal_contribution(self):
        self.assertTrue(monitor.marginal_contribution(0.85, 0.80)["positive"])
        self.assertFalse(monitor.marginal_contribution(0.75, 0.80)["positive"])

    def test_is_stale(self):
        self.assertTrue(monitor.is_stale({"independent_new_tp": 0, "last_active_days_ago": 31}))
        self.assertFalse(monitor.is_stale({"independent_new_tp": 1, "last_active_days_ago": 31}))
        self.assertFalse(monitor.is_stale({"independent_new_tp": 0, "last_active_days_ago": 5}))

    def test_forgetting_trend(self):
        series = [
            {"python": 0.9, "go": 0.8},
            {"python": 0.85, "go": 0.8},
            {"python": 0.80, "go": 0.8},
        ]
        self.assertEqual(["python"], monitor.forgetting_trend(series))


class ExperienceScopeStoreTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_default_scope_and_promotion(self):
        self.store.record_experience(
            "t1", "repo/a", "task-1", "feedback", "missed_issue",
            "rule_candidate", "fp-1", {"finding": {"rule_id": "SEC-X"}},
            "eval(x)", 0.9, "observed",
        )
        exp = self.store.list_experiences("t1")[0]
        self.assertEqual("repository-local", exp["scope"])
        self.assertEqual(1, self.store.promote_experience_scope("t1", "fp-1", "tenant-shared"))
        self.assertEqual("tenant-shared", self.store.list_experiences("t1")[0]["scope"])


if __name__ == "__main__":
    unittest.main()
