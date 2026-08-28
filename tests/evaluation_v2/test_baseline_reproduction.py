"""tests/evaluation_v2/test_baseline_reproduction.py

Reproduce the historical Milestone-1 numbers (Single-Agent 71.4% F1, Legacy
Multi-Agent 82.5% F1) on the *frozen* 100-PR dataset (plan phase 7 / section 2.2,
Rule 3).  No harness, no evolution — just the two legacy systems over the frozen
ground truth.
"""
import os
import sys
import tempfile
import unittest
from os.path import abspath, dirname, join

ROOT = dirname(dirname(dirname(abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evoagent.evaluation_v2.adapters import (  # noqa: E402
    LegacyMultiAgentEvaluationAdapter,
    SingleAgentEvaluationAdapter,
)
from evoagent.evaluation_v2.experiment import DATASET_SHA256, evaluate, load_dataset  # noqa: E402

DATASET = join(ROOT, "evaluation_data", "pr_diff_100.jsonl")


@unittest.skipUnless(os.path.exists(DATASET), "frozen dataset not present")
class BaselineReproductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_dataset(DATASET, verify_sha=DATASET_SHA256)
        cls._tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _f1(self, result):
        return result["metrics"]["detection"]["f1"]

    def test_dataset_fingerprint_is_frozen(self):
        from evoagent.evaluation_harness import dataset_fingerprint
        self.assertEqual(DATASET_SHA256, dataset_fingerprint(self.cases))

    def test_dataset_is_100_cases_10_repos(self):
        self.assertEqual(100, len(self.cases))
        self.assertEqual(10, len({c["repository"] for c in self.cases}))
        self.assertEqual(40, sum(bool(c["expected_findings"]) for c in self.cases))

    def test_single_agent_reproduces_714_f1(self):
        result = evaluate(SingleAgentEvaluationAdapter(), self.cases,
                          name="baseline", out_dir=self._tmp.name)
        self.assertAlmostEqual(0.7143, self._f1(result), places=4)

    def test_legacy_multi_agent_reproduces_825_f1(self):
        result = evaluate(LegacyMultiAgentEvaluationAdapter(), self.cases,
                          name="legacy_multi_agent", out_dir=self._tmp.name)
        self.assertAlmostEqual(0.8250, self._f1(result), places=4)

    def test_legacy_beats_single_on_high_risk_recall(self):
        single = evaluate(SingleAgentEvaluationAdapter(), self.cases,
                          name="baseline", out_dir=self._tmp.name)
        legacy = evaluate(LegacyMultiAgentEvaluationAdapter(), self.cases,
                          name="legacy_multi_agent", out_dir=self._tmp.name)
        single_hr = single["metrics"]["detection"]["high_risk_recall"]
        legacy_hr = legacy["metrics"]["detection"]["high_risk_recall"]
        self.assertGreater(legacy_hr, single_hr)


if __name__ == "__main__":
    unittest.main()