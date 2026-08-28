"""tests/evaluation_v2/test_harness_execution.py

Drive the *real* production harness path (ReviewService -> ExecutionContext ->
Coordinator -> ReviewHarness) and assert it produces governed, attributable
artifacts — DecisionTrace + ReplaySnapshot + resolved policy — which the legacy
reviewers can never produce (plan phase 8, section 7.2).
"""
import gc
import shutil
import sys
import tempfile
import time
import unittest
from os.path import abspath, dirname, join

ROOT = dirname(dirname(dirname(abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TEST_DIR = dirname(abspath(__file__))
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

from evoagent.evaluation_v2.adapters import (  # noqa: E402
    CurrentHarnessEvaluationAdapter,
)
from evoagent.evaluation_v2.experiment import DATASET_SHA256, load_dataset  # noqa: E402

from helpers import make_clean_case, make_risk_case  # noqa: E402


class HarnessExecutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = join(self._tmp.name, "eval.db")

    def tearDown(self):
        # SQLite/duckdb handles can linger on Windows after service.close();
        # force GC then retry the rmtree, ignoring any residual lock so the
        # assertions are not shadowed by a cleanup-only failure.
        gc.collect()
        for _ in range(5):
            try:
                shutil.rmtree(self._tmp.name)
                return
            except PermissionError:
                time.sleep(0.2)
                gc.collect()

    def test_harness_detects_eval_and_emits_telemetry(self):
        adapter = CurrentHarnessEvaluationAdapter(self._db)
        try:
            case = make_risk_case(added_line="    result = eval(value)\n")
            result = adapter.review_case(case)
            self.assertTrue(result.success)
            self.assertTrue(result.findings, "harness should detect the eval")
            self.assertTrue(result.decision_trace_created, "trace must be persisted")
            self.assertTrue(result.replay_snapshot_created, "snapshot must be persisted")
            self.assertTrue(result.policy_id, "policy attribution required")
            self.assertGreaterEqual(result.trace_event_count, 0)
        finally:
            adapter.close()

    def test_harness_resolves_repeatable_policy_context(self):
        adapter = CurrentHarnessEvaluationAdapter(self._db)
        try:
            result = adapter.review_case(
                make_clean_case(case_id="pr-0098"))
            self.assertEqual([], result.findings)
            detail = result.resolved_policy
            self.assertGreaterEqual(detail.get("max_steps", 0), 1)
            self.assertIn("enabled_agents", detail)
        finally:
            adapter.close()

    def test_over_all_frozen_validation_runs_clean(self):
        dataset = join(ROOT, "evaluation_data", "pr_diff_100.jsonl")
        cases = [c for c in load_dataset(dataset, DATASET_SHA256)
                 if c["split"] == "validation"][:8]
        adapter = CurrentHarnessEvaluationAdapter(self._db)
        try:
            for case in cases:
                result = adapter.review_case(case)
                self.assertTrue(result.success, case["id"])
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()