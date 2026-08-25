"""Tests for the policy canary (plan section 9.7)."""
import unittest

from evoagent.policy_evolution.canary import (
    CanaryConfig,
    CanaryVerdict,
    PolicyCanary,
)
from evoagent.policy_evolution.objective import EvolutionMetrics


def sample(quality=0.7, recall=0.8, cost=5.0, latency=2.0,
           failure_rate=0.0, reliability=1.0):
    return EvolutionMetrics(
        quality_score=quality, high_risk_recall=recall,
        reliability_score=reliability, failure_rate=failure_rate,
        cost=cost, latency=latency)


class PolicyCanaryTest(unittest.TestCase):

    def test_promotes_on_improvement(self):
        canary = PolicyCanary(CanaryConfig(promotion_threshold=0.05))
        canary.record("baseline", sample(quality=0.7, cost=5.0))
        canary.record("baseline", sample(quality=0.7, cost=5.0))
        canary.record("candidate", sample(quality=0.9, cost=4.0))
        canary.record("candidate", sample(quality=0.95, cost=3.0))
        decision = canary.decide()
        self.assertEqual(decision.verdict, CanaryVerdict.PROMOTE)

    def test_rolls_back_on_degradation(self):
        canary = PolicyCanary(CanaryConfig(promotion_threshold=0.05))
        canary.record("baseline", sample(quality=0.7, cost=5.0))
        canary.record("candidate", sample(quality=0.4, cost=5.0))
        decision = canary.decide()
        self.assertEqual(decision.verdict, CanaryVerdict.ROLLBACK)
        self.assertIn("degraded", decision.reasons[0])

    def test_holds_without_clear_signal(self):
        canary = PolicyCanary(CanaryConfig(promotion_threshold=0.1))
        canary.record("baseline", sample(quality=0.7))
        canary.record("candidate", sample(quality=0.71))
        decision = canary.decide()
        self.assertEqual(decision.verdict, CanaryVerdict.HOLD)

    def test_failure_rate_always_rolls_back(self):
        canary = PolicyCanary()
        canary.record("baseline", sample(quality=0.9))
        canary.record("candidate", sample(quality=0.95, failure_rate=0.05))
        decision = canary.decide()
        self.assertEqual(decision.verdict, CanaryVerdict.ROLLBACK)
        self.assertTrue(any("failure" in r.lower() for r in decision.reasons))

    def test_empty_candidate_holds(self):
        canary = PolicyCanary()
        decision = canary.decide()
        self.assertEqual(decision.verdict, CanaryVerdict.HOLD)
        self.assertEqual(decision.observed, 0)

    def test_sample_size_gate(self):
        canary = PolicyCanary(CanaryConfig(sample_size=2,
                                           promotion_threshold=0.3))
        canary.record("baseline", sample(quality=0.7))
        canary.record("baseline", sample(quality=0.7))
        canary.record("candidate", sample(quality=0.8))
        decision = canary.decide()
        self.assertEqual(decision.verdict, CanaryVerdict.HOLD)


if __name__ == "__main__":
    unittest.main()