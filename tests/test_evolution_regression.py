"""Tests for segment-level regression attribution (plan section 10.5)."""
import unittest

from evoagent.evolution_gov.regression import RegressionLocator


class RegressionAttributionTest(unittest.TestCase):

    def test_no_regression(self):
        locator = RegressionLocator(tolerance=0.06)
        baseline = {"language": {"python": 0.8, "java": 0.75}}
        candidate = {"language": {"python": 0.85, "java": 0.78}}
        result = locator.locate(baseline, candidate)
        self.assertFalse(result.regressed)
        self.assertEqual(result.offending(), [])

    def test_detects_single_segment_regression(self):
        locator = RegressionLocator(tolerance=0.06)
        baseline = {"language": {"python": 0.8, "java": 0.9}}
        candidate = {"language": {"python": 0.82, "java": 0.8}}
        result = locator.locate(baseline, candidate)
        self.assertTrue(result.regressed)
        offending = result.offending()
        self.assertEqual(len(offending), 1)
        self.assertEqual(offending[0].dimension, "language")
        self.assertEqual(offending[0].segment, "java")

    def test_delta_threshold(self):
        locator = RegressionLocator(tolerance=0.1)
        baseline = {"repo": {"alpha": 0.9}}
        candidate = {"repo": {"alpha": 0.85}}  # only 5pp drop
        self.assertFalse(locator.locate(baseline, candidate).regressed)


if __name__ == "__main__":
    unittest.main()