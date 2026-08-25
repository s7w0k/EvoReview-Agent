"""Closed-loop WP5: unified shadow loop, gate persistence and richer observations."""
import os
import tempfile
import unittest

from evoagent import evolution_gates as gates
from evoagent.store import TaskStore


class ShadowGateTests(unittest.TestCase):
    def test_passes_with_sufficient_samples(self):
        observations = [
            {"candidate_failed": False, "feedback_category": "accepted", "accepted": True,
             "latency_ms": 10.0, "cost_estimate": 1.0, "metrics": {}},
            {"candidate_failed": False, "feedback_category": "accepted", "accepted": True,
             "latency_ms": 11.0, "cost_estimate": 1.0, "metrics": {}},
        ]
        result = gates.shadow_gate(
            observations, thresholds={"min_samples": 2, "min_labeled": 1})
        self.assertTrue(result["passed"])
        self.assertEqual(2, result["checks"]["samples"])

    def test_blocks_insufficient_samples(self):
        result = gates.shadow_gate([], thresholds={"min_samples": 2})
        self.assertFalse(result["passed"])
        self.assertTrue(any("samples" in r for r in result["reasons"]))

    def test_blocks_high_risk_miss(self):
        observations = [
            {"candidate_failed": False, "feedback_category": "accepted", "accepted": True,
             "metrics": {"high_risk_missed": 1}},
        ]
        result = gates.shadow_gate(
            observations,
            thresholds={"min_samples": 1, "min_labeled": 1, "high_risk_missed_max": 0})
        self.assertFalse(result["passed"])
        self.assertTrue(any("high-risk" in r for r in result["reasons"]))

    def test_blocks_false_positive_budget(self):
        observations = [
            {"candidate_failed": False, "feedback_category": "false_positive", "accepted": False},
            {"candidate_failed": False, "feedback_category": "accepted", "accepted": True},
        ]
        result = gates.shadow_gate(
            observations,
            thresholds={"min_samples": 2, "min_labeled": 2, "fp_rate_budget_pp": 0.0},
            baseline={"fp_rate": 0.0})
        self.assertFalse(result["passed"])
        self.assertTrue(any("false-positive" in r for r in result["reasons"]))


class StoreShadowTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_gate_result_round_trip(self):
        saved = self.store.save_gate_result({
            "tenant_id": "t1", "job_id": "job-1", "candidate_kind": "prompt",
            "candidate_name": "llm-review", "candidate_version": 3, "stage": "shadow",
            "gate_name": "shadow_to_canary", "passed": False,
            "threshold": {"min_samples": 100}, "evidence": {"samples": 12},
        })
        self.assertFalse(saved["passed"])
        self.assertEqual({"min_samples": 100}, saved["threshold"])
        self.assertEqual(12, saved["evidence"]["samples"])
        self.assertEqual(1, len(self.store.list_gate_results("t1", stage="shadow")))

    def test_backfill_observation(self):
        self.store.record_shadow_observation(
            "t1", "skill", "task-1", "stable", {"finding": "x"}, {"finding": "y"},
            1.0, candidate_version=2, latency_ms=5.0, metrics={"high_risk_missed": 0},
        )
        self.assertTrue(self.store.backfill_release_observation(
            "t1", "skill", "task-1", "ok", "accepted", True))
        obs = self.store.list_release_observations("t1", "skill")[0]
        self.assertEqual("accepted", obs["feedback_category"])
        self.assertTrue(obs["accepted"])
        self.assertEqual(2, obs["candidate_version"])


if __name__ == "__main__":
    unittest.main()
