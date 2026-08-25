"""Tests for automatic policy rollback (plan section 9.7: auto rollback)."""
import unittest

from evoagent.policy_evolution.rollback import (
    AutoRollback,
    RollbackThresholds,
)
from evoagent.policy_evolution.objective import EvolutionMetrics


def metrics(quality=0.8, recall=0.9, reliability=1.0, failure=0.0,
            critical=0):
    return EvolutionMetrics(
        quality_score=quality, high_risk_recall=recall,
        reliability_score=reliability, failure_rate=failure,
        critical_misses=critical)


class AutoRollbackTest(unittest.TestCase):

    def test_keeps_healthy_candidate(self):
        baseline = metrics()
        candidate = EvolutionMetrics(quality_score=0.85, high_risk_recall=0.9,
                                     reliability_score=1.0, failure_rate=0.0,
                                     cost=3.0, latency=1.0)
        decision = AutoRollback().evaluate(baseline, candidate)
        self.assertFalse(decision.should_rollback)

    def test_rolls_back_on_critical_miss(self):
        baseline = metrics()
        candidate = metrics(critical=1)
        decision = AutoRollback().evaluate(baseline, candidate)
        self.assertTrue(decision.should_rollback)
        self.assertTrue(any("critical" in r.lower() for r in decision.reasons))

    def test_rolls_back_on_failure_rate(self):
        baseline = metrics()
        candidate = metrics(failure=0.05)
        decision = AutoRollback(RollbackThresholds(
            failure_rate_ceiling=0.01)).evaluate(baseline, candidate)
        self.assertTrue(decision.should_rollback)

    def test_rolls_back_on_recall_regression(self):
        baseline = metrics(recall=0.9)
        candidate = metrics(recall=0.8)
        decision = AutoRollback(RollbackThresholds(
            recall_regression=0.01)).evaluate(baseline, candidate)
        self.assertTrue(decision.should_rollback)

    def test_rolls_back_on_quality_regression(self):
        baseline = metrics(quality=0.8)
        candidate = metrics(quality=0.7)
        decision = AutoRollback(RollbackThresholds(
            quality_regression=0.02)).evaluate(baseline, candidate)
        self.assertTrue(decision.should_rollback)


if __name__ == "__main__":
    unittest.main()