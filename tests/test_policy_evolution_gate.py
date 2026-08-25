"""Tests for the hard-safety evolution gate (plan section 9.4)."""
import unittest

from evoagent.policy_evolution.gate import EvolutionGate
from evoagent.policy_evolution.objective import EvolutionMetrics


def good_metrics(**over):
    value = {
        "quality_score": 0.8,
        "high_risk_recall": 0.9,
        "critical_misses": 0,
        "reliability_score": 1.0,
        "cost": 5.0,
        "latency": 2.0,
    }
    value.update(over)
    return EvolutionMetrics(**value)


class EvolutionGateTest(unittest.TestCase):

    def test_passing_candidate_approved(self):
        gate = EvolutionGate()
        baseline = good_metrics()
        candidate = good_metrics(quality_score=0.85, cost=3.0)
        decision = gate.evaluate(baseline, candidate)
        self.assertTrue(decision.approved)

    def test_critical_miss_rejected_even_if_cheaper(self):
        gate = EvolutionGate()
        baseline = good_metrics()
        candidate = good_metrics(critical_misses=1, cost=0.1)
        decision = gate.evaluate(baseline, candidate)
        self.assertFalse(decision.approved)
        self.assertTrue(any("critical" in reason.lower() for reason in decision.reasons))

    def test_high_risk_recall_regression_rejected(self):
        gate = EvolutionGate(high_risk_recall_tolerance=0.01)
        baseline = good_metrics(high_risk_recall=0.9)
        candidate = good_metrics(high_risk_recall=0.85)  # 5pp drop
        decision = gate.evaluate(baseline, candidate)
        self.assertFalse(decision.approved)

    def test_recall_within_tolerance_accepted(self):
        gate = EvolutionGate(high_risk_recall_tolerance=0.01)
        baseline = good_metrics(high_risk_recall=0.9)
        candidate = good_metrics(high_risk_recall=0.895)
        self.assertTrue(gate.evaluate(baseline, candidate).approved)

    def test_safety_outranks_utility(self):
        # Even a big cost win cannot override a safety regression.
        gate = EvolutionGate()
        baseline = good_metrics()
        candidate = good_metrics(critical_misses=2, cost=0.0,
                                 quality_score=0.99)
        self.assertFalse(gate.evaluate(baseline, candidate).approved)


if __name__ == "__main__":
    unittest.main()