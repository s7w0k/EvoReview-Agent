"""Closed-loop WP3: dataset layering, stratified metrics and quality gates.

Covers forgetting/generalization/production-source gates, dataset leakage
detection, and the importer's source/dedup/label-location/leakage helpers.
"""
import importlib.util
import os
import tempfile
import unittest

from evoagent import evolution_gates as gates
from evoagent.evolution import RegressionEvaluator
from evoagent.store import TaskStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_importer():
    path = os.path.join(ROOT, "scripts", "import_github_pr_dataset.py")
    spec = importlib.util.spec_from_file_location("import_github_pr_dataset", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metrics(stratified=None, fix_correctness=None):
    return {"stratified": stratified or {}, "fix_correctness": fix_correctness}


class StratifiedMetricsTests(unittest.TestCase):
    def test_stratified_metrics_by_language_rule_and_severity(self):
        cases = [
            {"name": "a", "split": "validation", "language": "python",
             "repository": "r1", "suite_id": "real-validation", "diff": "x",
             "expected": [{"path": "a.py", "line": 1, "rule_id": "SEC-X",
                           "min_severity": "high"}]},
            {"name": "b", "split": "validation", "language": "go",
             "repository": "r2", "suite_id": "real-validation", "diff": "y",
             "expected": []},
        ]
        results = [
            {"tp": 1, "fp": 0, "fn": 0},
            {"tp": 0, "fp": 0, "fn": 0},
        ]
        stratified = gates.stratified_metrics(cases, results)
        self.assertEqual(1.0, stratified["language"]["python"]["f1"])
        self.assertEqual(1.0, stratified["language"]["go"]["f1"])
        self.assertEqual(1.0, stratified["rule"]["SEC-X"]["f1"])
        self.assertIn("high", stratified["severity"])
        self.assertIn("clean", stratified["severity"])


class ForgettingGateTests(unittest.TestCase):
    def test_blocks_language_f1_regression(self):
        baseline = _metrics(stratified={"language": {"python": {"f1": 0.9, "recall": 0.9}}})
        candidate = _metrics(stratified={"language": {"python": {"f1": 0.85, "recall": 0.85}}})
        result = gates.forgetting_gate(baseline, candidate)
        self.assertFalse(result["passed"])
        self.assertTrue(any("language" in r for r in result["reasons"]))

    def test_blocks_golden_critical_recall_drop(self):
        baseline = _metrics(stratified={"suite": {"golden-regression": {"f1": 1.0, "recall": 1.0}}})
        candidate = _metrics(stratified={"suite": {"golden-regression": {"f1": 0.8, "recall": 0.5}}})
        result = gates.forgetting_gate(baseline, candidate)
        self.assertFalse(result["passed"])
        self.assertTrue(any("golden" in r for r in result["reasons"]))

    def test_passes_when_no_strata_regress(self):
        baseline = _metrics(stratified={"language": {"python": {"f1": 0.9, "recall": 0.9}}})
        candidate = _metrics(stratified={"language": {"python": {"f1": 0.95, "recall": 0.95}}})
        self.assertTrue(gates.forgetting_gate(baseline, candidate)["passed"])


class GeneralizationGateTests(unittest.TestCase):
    def test_blocks_cross_repo_regression(self):
        baseline = _metrics(stratified={"suite": {"cross-repo-transfer": {"f1": 0.8}}})
        candidate = _metrics(stratified={"suite": {"cross-repo-transfer": {"f1": 0.7}}})
        result = gates.generalization_gate(baseline, candidate)
        self.assertFalse(result["passed"])
        self.assertTrue(any("cross-repo" in r for r in result["reasons"]))

    def test_blocks_key_domain_degradation(self):
        baseline = _metrics(stratified={"language": {"python": {"f1": 0.9}}})
        candidate = _metrics(stratified={"language": {"python": {"f1": 0.85}}})
        result = gates.generalization_gate(baseline, candidate)
        self.assertFalse(result["passed"])


class ProductionSourceGateTests(unittest.TestCase):
    def test_rejects_synthetic_only(self):
        synthetic = [{"source": "builtin", "source_uri": ""}]
        self.assertFalse(gates.production_source_gate(synthetic)["passed"])

    def test_accepts_real_source(self):
        real = [{"source": "github-real", "source_uri": "https://github.com/a/b/pull/1"}]
        self.assertTrue(gates.production_source_gate(real)["passed"])


class DatasetLeakageTests(unittest.TestCase):
    def test_detects_same_repo_across_splits(self):
        cases = [
            {"name": "v", "split": "validation", "repository": "a/b", "diff": "x", "expected": []},
            {"name": "h", "split": "holdout", "repository": "a/b", "diff": "y", "expected": []},
        ]
        issues = gates.detect_dataset_leakage(cases)
        self.assertTrue(any(i["type"] == "same_repository" for i in issues))

    def test_detects_same_diff_across_splits(self):
        cases = [
            {"name": "v", "split": "validation", "repository": "a/b", "diff": "same", "expected": []},
            {"name": "h", "split": "holdout", "repository": "c/d", "diff": "same", "expected": []},
        ]
        issues = gates.detect_dataset_leakage(cases)
        self.assertTrue(any(i["type"] == "same_diff" for i in issues))

    def test_detects_derived_sample_across_splits(self):
        finding = {"path": "a.py", "line": 1}
        cases = [
            {"name": "v", "split": "validation", "repository": "a/b", "diff": "x",
             "expected": [finding]},
            {"name": "h", "split": "holdout", "repository": "a/b", "diff": "y",
             "expected": [finding]},
        ]
        issues = gates.detect_dataset_leakage(cases)
        self.assertTrue(any(i["type"] == "derived_sample" for i in issues))


class RegressionEvaluatorStratifiedTests(unittest.TestCase):
    def test_run_includes_stratified_metrics(self):
        class _EmptyReviewer:
            name = "empty"

            def review(self, diff, parsed):
                return []

        cases = [
            {"name": "a", "split": "validation", "language": "python",
             "repository": "r1", "suite_id": "real-validation",
             "diff": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(x)\n",
             "expected": [{"path": "a.py", "line": 1, "min_severity": "high"}]},
        ]
        metrics = RegressionEvaluator(lambda _p: _EmptyReviewer()).run("p", cases)
        self.assertIn("stratified", metrics)
        self.assertIn("python", metrics["stratified"]["language"])


class ImporterHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.importer = _load_importer()

    def test_validate_source(self):
        good = {"repository": "owner/name", "pull_request": 1,
                "source_uri": "https://github.com/owner/name/pull/1"}
        self.importer.validate_source(good)
        with self.assertRaises(ValueError):
            self.importer.validate_source({"repository": "bad", "pull_request": 1,
                                           "source_uri": "https://x"})

    def test_deduplicate_by_diff(self):
        records = [
            {"diff": "a", "id": "1"}, {"diff": "a", "id": "2"}, {"diff": "b", "id": "3"},
        ]
        self.assertEqual(["1", "3"], [r["id"] for r in self.importer.deduplicate_by_diff(records)])

    def test_dataset_version_fingerprint_stable(self):
        records = [{"repository": "a/b", "pull_request": 1, "split": "validation",
                    "diff": "x", "expected_findings": []}]
        self.assertEqual(
            self.importer.dataset_version_fingerprint(records),
            self.importer.dataset_version_fingerprint(records),
        )

    def test_assert_no_repo_leakage(self):
        self.importer.assert_no_repo_leakage([
            {"repository": "a/b", "split": "validation"},
            {"repository": "c/d", "split": "holdout"},
        ])
        with self.assertRaises(ValueError):
            self.importer.assert_no_repo_leakage([
                {"repository": "a/b", "split": "validation"},
                {"repository": "a/b", "split": "holdout"},
            ])

    def test_check_label_locations(self):
        from evoagent.diff_parser import parse_unified_diff
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(x)\n"
        parsed = parse_unified_diff(diff)
        self.importer.check_label_locations(
            {"expected_findings": [{"path": "a.py", "start_line": 1}]}, parsed)
        with self.assertRaises(ValueError):
            self.importer.check_label_locations(
                {"expected_findings": [{"path": "a.py", "start_line": 99}]}, parsed)


if __name__ == "__main__":
    unittest.main()
