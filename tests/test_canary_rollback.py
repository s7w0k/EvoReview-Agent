"""Closed-loop WP6: staged canary and automatic quality rollback."""
import os
import tempfile
import unittest

from evoagent import canary
from evoagent.rollout import ReleaseManager
from evoagent.store import TaskStore


class CanaryStageMachineTests(unittest.TestCase):
    def test_next_stage_sequence(self):
        self.assertEqual((5, 50), canary.next_stage(0))
        self.assertEqual((20, 200), canary.next_stage(5))
        self.assertEqual((50, 500), canary.next_stage(20))
        self.assertEqual((100, None), canary.next_stage(50))
        self.assertIsNone(canary.next_stage(100))

    def test_should_advance_requires_tasks_and_gates(self):
        self.assertFalse(canary.should_advance(0, 49, True))
        self.assertFalse(canary.should_advance(0, 50, False))
        self.assertTrue(canary.should_advance(0, 50, True))


class TechnicalRollbackTests(unittest.TestCase):
    def test_consecutive_failures(self):
        results = [{"failed": True}, {"failed": True}, {"failed": True}]
        reasons = canary.technical_rollback_reasons({}, results)
        self.assertTrue(any("consecutively" in r for r in reasons))

    def test_error_rate(self):
        results = [{"failed": True}, {"failed": False}] * 25  # 25 errors / 50
        reasons = canary.technical_rollback_reasons({}, results)
        self.assertTrue(any("error rate" in r for r in reasons))

    def test_unauthorized_permission(self):
        reasons = canary.technical_rollback_reasons({}, [{"unauthorized_permission": True}])
        self.assertTrue(any("permission" in r for r in reasons))

    def test_latency_and_cost(self):
        results = [{"latency_ms": 200.0}, {"cost": 10.0}]
        reasons = canary.technical_rollback_reasons(
            {}, results, stable_p95_ms=100.0, hard_cost_budget=5.0)
        self.assertTrue(any("latency" in r for r in reasons))
        self.assertTrue(any("cost" in r for r in reasons))

    def test_isolation_and_artifact(self):
        results = [{"isolation_anomaly": True}, {"artifact_mismatch": True}]
        reasons = canary.technical_rollback_reasons({}, results)
        self.assertTrue(any("isolation" in r for r in reasons))
        self.assertTrue(any("fingerprint" in r for r in reasons))


class QualityRollbackTests(unittest.TestCase):
    def test_high_risk_miss(self):
        reasons = canary.quality_rollback_reasons({}, {"high_risk_missed": 1})
        self.assertTrue(any("high-risk" in r for r in reasons))

    def test_false_positive_budget(self):
        reasons = canary.quality_rollback_reasons(
            {}, {"stable_fp_rate": 0.01, "candidate_fp_rate": 0.05})
        self.assertTrue(any("false-positive" in r for r in reasons))

    def test_accept_rate_drop(self):
        reasons = canary.quality_rollback_reasons(
            {}, {"stable_accept_rate": 0.9, "candidate_accept_rate": 0.8})
        self.assertTrue(any("accept rate" in r for r in reasons))


class ReleaseManagerCanaryTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)
        self.releases = ReleaseManager(self.store)

    def tearDown(self):
        os.unlink(self.path)

    def _seed_running(self):
        self.store.save_deployment("t1", "skill", {
            "stable_version": 1, "candidate_version": 2,
            "canary_percent": 0, "status": "running",
        })

    def test_advance_canary_stages(self):
        self._seed_running()
        self.assertTrue(self.releases.advance_canary("t1", "skill", 50, True))
        self.assertEqual(5, self.store.get_deployment("t1", "skill")["canary_percent"])
        self.assertTrue(self.releases.advance_canary("t1", "skill", 200, True))
        self.assertEqual(20, self.store.get_deployment("t1", "skill")["canary_percent"])

    def test_pause_blocks_advance(self):
        self._seed_running()
        self.assertTrue(self.releases.pause_canary("t1", "skill", "admin"))
        self.assertFalse(self.releases.advance_canary("t1", "skill", 50, True))
        self.assertTrue(self.releases.resume_canary("t1", "skill"))
        self.assertTrue(self.releases.advance_canary("t1", "skill", 50, True))

    def test_rollback_atomic(self):
        self._seed_running()
        result = self.releases.rollback("t1", "skill", "high-risk miss", {"fp": 0.1})
        self.assertEqual("rolled_back", result["status"])
        self.assertEqual(0, result["canary_percent"])
        self.assertEqual(0, result["shadow_percent"])
        self.assertEqual("high-risk miss", result["rollback_reason"])
        self.assertEqual({"fp": 0.1}, result["last_gate_result"])


if __name__ == "__main__":
    unittest.main()
