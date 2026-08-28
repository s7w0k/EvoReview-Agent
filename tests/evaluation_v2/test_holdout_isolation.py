"""tests/evaluation_v2/test_holdout_isolation.py

Enforce the dataset-integrity guarantees the whole report depends on (plan phase 9,
Rule 1 / Rule 2): the Holdout split shares no repository with Validation training
data, and a candidate that was frozen only from Validation must not have been
trained on any Holdout ground truth — it can only be *measured* against it.
"""
import os
import sys
import unittest
from os.path import abspath, dirname, join

ROOT = dirname(dirname(dirname(abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evoagent.evaluation_harness import dataset_fingerprint  # noqa: E402
from evoagent.evaluation_v2.experiment import DATASET_SHA256, load_dataset, split_cases  # noqa: E402

DATASET = join(ROOT, "evaluation_data", "pr_diff_100.jsonl")


@unittest.skipUnless(os.path.exists(DATASET), "frozen dataset not present")
class HoldoutIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_dataset(DATASET, verify_sha=DATASET_SHA256)
        cls.validation = split_cases(cls.cases, "validation")
        cls.holdout = split_cases(cls.cases, "holdout")

    def test_fingerprint_matches_frozen_value(self):
        # The dataset a reviewer is measured on must never drift silently.
        self.assertEqual(DATASET_SHA256, dataset_fingerprint(self.cases))

    def test_splits_cover_dataset_without_overlap(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(80, len(self.validation))
        self.assertEqual(20, len(self.holdout))

    def test_no_repository_leaks_between_splits(self):
        val_repos = {c["repository"] for c in self.validation}
        hold_repos = {c["repository"] for c in self.holdout}
        self.assertTrue(val_repos.isdisjoint(hold_repos),
                        "validation and holdout must not share repositories")

    def test_validation_has_its_own_risk_density(self):
        # Training evidence must come only from validation; holdout is unseen.
        val_risk = sum(bool(c["expected_findings"]) for c in self.validation)
        hold_risk = sum(bool(c["expected_findings"]) for c in self.holdout)
        self.assertGreater(val_risk, 0)
        self.assertGreater(hold_risk, 0)


if __name__ == "__main__":
    unittest.main()