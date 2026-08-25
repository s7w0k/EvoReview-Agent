import unittest

from evoagent.replay.comparator import ReplayComparator


class ReplayComparatorTest(unittest.TestCase):
    def setUp(self):
        self.comparator = ReplayComparator(min_quality_improvement=0.02, max_regression=0.01)

    def test_pass_on_improvement(self):
        baseline = {"finding_f1": 0.8, "high_risk_recall": 0.9, "tool_calls": 10}
        candidate = {"finding_f1": 0.84, "high_risk_recall": 0.91, "tool_calls": 9}
        decision = self.comparator.decide(baseline, candidate)
        self.assertEqual(decision["decision"], "PASS")

    def test_high_risk_recall_regression_fails(self):
        baseline = {"finding_f1": 0.7, "high_risk_recall": 0.9}
        candidate = {"finding_f1": 0.8, "high_risk_recall": 0.8}
        decision = self.comparator.decide(baseline, candidate)
        self.assertEqual(decision["decision"], "FAIL")
        self.assertIn("high-risk recall", decision["reason"])

    def test_quality_below_threshold_fails(self):
        baseline = {"finding_f1": 0.8, "high_risk_recall": 0.9}
        candidate = {"finding_f1": 0.81, "high_risk_recall": 0.9}
        decision = self.comparator.decide(baseline, candidate)
        self.assertEqual(decision["decision"], "FAIL")

    def test_blocked_metric_regression_fails(self):
        baseline = {"finding_f1": 0.85, "high_risk_recall": 0.9}
        candidate = {"finding_f1": 0.87, "high_risk_recall": 0.88}
        # high_risk_recall dropped by more than the safety bound.
        decision = self.comparator.decide(baseline, candidate)
        self.assertEqual(decision["decision"], "FAIL")


if __name__ == "__main__":
    unittest.main()