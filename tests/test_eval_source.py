"""Work Package 8: real dataset source switching and continuous evaluation metrics.

Covers the plan's acceptance items:
- real labelled samples import and evaluate under source="github-real";
- new metric keys round-trip in SQLite (PostgreSQL mirrors the contract) and
  legacy run records read without error when the keys are absent;
- insufficient github-real samples keep the evolution candidate "deferred"
  without polluting the default builtin path;
- the builtin dark-switch default stays unchanged.
"""
import importlib.util
import json
import os
import tempfile
import unittest

from evoagent.config import Settings
from evoagent.evaluation_benchmark import generate_controlled_pr_cases
from evoagent.evaluation_harness import EndToEndEvaluationHarness
from evoagent.evolution import EvolutionEngine, RegressionEvaluator
from evoagent.models import Finding, Severity
from evoagent.skill_evolution import SkillEvolutionEngine, validate_artifact
from evoagent.store import TaskStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Default builtin corpus (seed_defaults=True) contains 5 validation and 2
# holdout cases; a real-PR sample must never change that by default.
BUILTIN_VALIDATION = 5
BUILTIN_HOLDOUT = 2

BASE_DIFF = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(data)\n"
BASE_EXPECTED = [{"path": "a.py", "line": 1, "min_severity": "high"}]

REAL_DIFF = (
    "--- a/worker.py\n+++ b/worker.py\n@@ -1 +1 @@\n-old\n+subprocess.run(cmd, shell=True)\n"
)
REAL_EXPECTED = [{
    "path": "worker.py", "line": 1, "rule_id": "SEC-SHELL-INJECTION",
    "min_severity": "high",
}]
REAL_HOLDOUT_DIFF = (
    "--- a/gateway.py\n+++ b/gateway.py\n@@ -1 +1 @@\n-old\n+eval(request_body)\n"
)
REAL_HOLDOUT_EXPECTED = [{
    "path": "gateway.py", "line": 1, "rule_id": "SEC-EVAL", "min_severity": "high",
}]


def load_import_script():
    path = os.path.join(ROOT, "scripts", "import_github_pr_dataset.py")
    spec = importlib.util.spec_from_file_location("import_github_pr_dataset", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvalSourceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def add_github_real_cases(self):
        self.store.save_evaluation_case(
            "real-pr-1", "validation", REAL_DIFF, REAL_EXPECTED,
            "github-real", True, "risk",
        )
        self.store.save_evaluation_case(
            "real-pr-2", "holdout", REAL_HOLDOUT_DIFF, REAL_HOLDOUT_EXPECTED,
            "github-real", True, "risk",
        )

    def test_default_builtin_ignores_github_real_samples(self):
        engine = EvolutionEngine(self.store, max_cases=20)
        before = engine.status()
        self.assertEqual(BUILTIN_VALIDATION, before["validation_cases"])
        self.assertEqual(BUILTIN_HOLDOUT, before["holdout_cases"])

        self.add_github_real_cases()

        after = engine.status()
        # The builtin dark switch must leave the legacy dataset untouched.
        self.assertEqual(
            before["validation_cases"], after["validation_cases"],
            "github-real samples leaked into the builtin evaluation path",
        )
        self.assertEqual(
            before["holdout_cases"], after["holdout_cases"],
            "github-real samples leaked into the builtin holdout path",
        )
        self.assertEqual(
            before["validation_dataset_fingerprint"],
            after["validation_dataset_fingerprint"],
        )

    def test_github_real_source_selects_only_real_samples(self):
        engine = EvolutionEngine(
            self.store, reviewer_factory=lambda _p: object(), min_cases=1,
            max_cases=20, seed_defaults=False, eval_source="github-real",
        )
        self.add_github_real_cases()
        status = engine.status()
        self.assertEqual(1, status["validation_cases"])
        self.assertEqual(1, status["holdout_cases"])
        self.assertNotEqual(0, status["ready"])

    def test_github_real_replay_records_dataset_source(self):
        class RealAwareReviewer:
            def __init__(self, prompt):
                self.prompt = prompt

            def review(self, _diff, parsed):
                if "improved" not in self.prompt:
                    return []
                line = parsed.added_lines[0]
                return [Finding(
                    "SEC-SHELL-INJECTION", Severity.HIGH, "shell",
                    "unsafe subprocess", line.path, line.line,
                    line.content, "shell=False", "regression test", 0.9,
                )]

        engine = EvolutionEngine(
            self.store, reviewer_factory=RealAwareReviewer, min_cases=1,
            max_cases=20, min_holdout_cases=1, seed_defaults=False,
            eval_source="github-real",
        )
        self.add_github_real_cases()
        result = engine.propose(
            "llm-review",
            "improved: Review the diff and return JSON with severity, fix and test.",
        )
        self.assertEqual("activated", result["decision"])
        run = self.store.list_evolution_runs()[0]
        self.assertEqual(
            "github-real",
            run["metrics"]["reproducibility"]["dataset_source"],
        )
        self.assertTrue(
            run["metrics"]["reproducibility"]["validation_dataset_sha256"]
        )

    def test_insufficient_github_real_keeps_candidate_deferred(self):
        self.store.save_evaluation_case(
            "only-real-1", "validation", REAL_DIFF, REAL_EXPECTED,
            "github-real", True, "risk",
        )

        class SilentReviewer:
            def __init__(self, _prompt):
                pass

            def review(self, _diff, _parsed):
                return []

        engine = EvolutionEngine(
            self.store, reviewer_factory=SilentReviewer, min_cases=3,
            max_cases=20, seed_defaults=False, eval_source="github-real",
        )
        result = engine.propose(
            "llm-review",
            "Review the diff and return JSON with severity, fix and test.",
        )
        self.assertEqual("deferred", result["decision"])
        self.assertIn("smaller", result["reason"])

        # The builtin path is not polluted: a default engine still sees its
        # own builtin corpus and is ready with a configured reviewer.
        builtin = EvolutionEngine(
            self.store, reviewer_factory=lambda _p: object(), min_cases=3,
            max_cases=20,
        )
        self.assertEqual(BUILTIN_VALIDATION, builtin.status()["validation_cases"])

    def test_new_metric_keys_round_trip_and_fix_correctness(self):
        self.store.record_failure_case("task-a", "accepted", {"note": "ok"})
        self.store.record_failure_case("task-a", "accepted", {"note": "ok"})
        self.store.record_failure_case("task-a", "bad_fix", {"note": "no"})

        class FindingReviewer:
            def __init__(self, _prompt):
                pass

            def review(self, _diff, parsed):
                line = parsed.added_lines[0]
                return [Finding(
                    "SEC-EVAL", Severity.CRITICAL, "eval", "danger",
                    line.path, line.line, line.content, "fix", "test", 0.9,
                )]

        case = {
            "id": 1, "name": "eval-case",
            "diff": BASE_DIFF, "expected": BASE_EXPECTED,
        }
        metrics = RegressionEvaluator(FindingReviewer).run(
            "prompt", [case], self.store,
        )
        self.assertIn("false_positive_rate", metrics)
        self.assertIn("latency_ms", metrics)
        self.assertIn("per_finding_cost_estimate", metrics)
        self.assertIn("fix_correctness", metrics)
        self.assertIsNone(metrics["per_finding_cost_estimate"])
        # 2 accepted / 3 total = 0.6667 (local mode reuses feedback stats).
        self.assertAlmostEqual(0.6667, metrics["fix_correctness"], places=4)

    def test_cost_estimate_reviewer_populates_per_finding_cost(self):
        class CostedReviewer:
            cost_estimate = 0.42

            def __init__(self, _prompt):
                pass

            def review(self, _diff, parsed):
                line = parsed.added_lines[0]
                return [Finding(
                    "SEC-EVAL", Severity.CRITICAL, "eval", "danger",
                    line.path, line.line, line.content, "fix", "test", 0.9,
                )]

        case = {
            "id": 1, "name": "eval-case",
            "diff": BASE_DIFF, "expected": BASE_EXPECTED,
        }
        metrics = RegressionEvaluator(CostedReviewer).run("prompt", [case])
        self.assertAlmostEqual(0.42, metrics["per_finding_cost_estimate"], places=6)

    def test_legacy_run_metrics_without_new_keys_read_safely(self):
        old_metrics = {
            "candidate": {"score": 0.9},
            "reproducibility": {"evaluation_schema_version": 1},
        }
        self.store.save_evolution_run({
            "id": "run-legacy", "skill_name": "llm-review",
            "candidate_version": 1, "baseline_version": None,
            "decision": "activated", "candidate_score": 0.9,
            "baseline_score": 0.8, "metrics": old_metrics,
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        runs = self.store.list_evolution_runs()
        self.assertEqual(1, len(runs))
        candidate = runs[0]["metrics"]["candidate"]
        # dict.get with default keeps legacy readers working.
        self.assertEqual(0.9, candidate.get("score"))
        self.assertIsNone(candidate.get("per_finding_cost_estimate"))

    def test_evolution_run_round_trips_new_keys(self):
        engine = EvolutionEngine(
            self.store, min_cases=1, max_cases=20, seed_defaults=False,
            eval_source="github-real",
        )
        self.add_github_real_cases()
        engine.propose(
            "llm-review",
            "Review the diff and return JSON with severity, fix and test.",
        )
        run = self.store.list_evolution_runs()[0]
        self.assertEqual(
            "github-real",
            run["metrics"]["reproducibility"]["dataset_source"],
        )


class EvalSourceSkillEvolutionTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_skill_evolution_respects_github_real_source(self):
        self.store.save_evaluation_case(
            "real-skill-1", "validation", REAL_DIFF, REAL_EXPECTED,
            "github-real", True, "risk",
        )
        self.store.save_evaluation_case(
            "real-skill-2", "holdout", REAL_HOLDOUT_DIFF, REAL_HOLDOUT_EXPECTED,
            "github-real", True, "risk",
        )
        engine = SkillEvolutionEngine(
            self.store, min_cases=1, min_holdout_cases=1, max_cases=20,
            eval_source="github-real",
        )
        status = engine.status()
        self.assertEqual(1, status["validation_cases"])
        self.assertEqual(1, status["holdout_cases"])

        artifact = validate_artifact({
            "name": "evolved-review",
            "description": "learns real PR risks",
            "rules": [{
                "rule_id": "SEC-SHELL-INJECTION", "severity": "high",
                "match": "subprocess.run(cmd, shell=True)",
                "title": "Unsafe subprocess",
                "explanation": "shell=True with a command string is unsafe.",
                "fix": "Use shell=False with an argument list.",
                "test": "Add a regression test.",
            }],
        }, "evolved-review")
        result = engine.propose("evolved-review", artifact)
        self.assertEqual("activated", result["decision"])
        run = self.store.list_skill_evolution_runs()[0]
        self.assertEqual(
            "github-real",
            run["metrics"]["reproducibility"]["dataset_source"],
        )
        self.assertTrue(run["metrics"]["reproducibility"]["dataset_sha256"])


class EvalSourceConfigAndPipelineTests(unittest.TestCase):
    def test_config_env_parses_and_validates_eval_source(self):
        os.environ["EVOAGENT_EVAL_SOURCE"] = "github-real"
        try:
            settings = Settings.from_env()
            self.assertEqual("github-real", settings.eval_source)
            settings.validate_evolution()
        finally:
            os.environ.pop("EVOAGENT_EVAL_SOURCE", None)

        os.environ["EVOAGENT_EVAL_SOURCE"] = "bogus"
        try:
            with self.assertRaisesRegex(ValueError, "EVOAGENT_EVAL_SOURCE"):
                Settings.from_env().validate_evolution()
        finally:
            os.environ.pop("EVOAGENT_EVAL_SOURCE", None)

    def test_import_script_converts_expected_and_categories(self):
        module = load_import_script()
        record = {
            "expected_findings": [{
                "path": "src/worker.py", "start_line": 3, "end_line": 3,
                "cwe": "CWE-78", "severity": "high", "rule_id": "SEC-SHELL-INJECTION",
            }],
        }
        expected = module.to_store_expected(record)
        self.assertEqual([{
            "path": "src/worker.py", "line": 3,
            "min_severity": "high", "rule_id": "SEC-SHELL-INJECTION",
        }], expected)
        self.assertEqual("risk", module.derive_category(record))
        self.assertEqual("clean", module.derive_category(
            {"expected_findings": []}
        ))
        self.assertEqual("refactor", module.derive_category(
            {"expected_findings": [{"path": "a", "start_line": 1, "end_line": 1}],
             "category": "refactor"}
        ))

    def test_harness_source_filter_selects_or_rejects_by_kind(self):
        cases = generate_controlled_pr_cases()
        baseline = EndToEndEvaluationHarness().run(
            type("R", (), {"name": "r", "review": lambda self, d, p: []})(),
            cases, "filtered", "synthetic-controlled",
        )
        self.assertEqual(100, baseline["dataset"]["cases"])
        with self.assertRaisesRegex(ValueError, "no evaluation cases match"):
            EndToEndEvaluationHarness().run(
                type("R", (), {"name": "r", "review": lambda self, d, p: []})(),
                cases, "empty", "github-real",
            )

    def test_github_real_import_writes_isolated_evaluation_cases(self):
        handle, db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            store = TaskStore(db_path)
            record = {
                "id": "org/repo#42", "split": "validation",
                "diff": REAL_DIFF,
                "expected_findings": [{
                    "path": "worker.py", "start_line": 1, "end_line": 1,
                    "cwe": "CWE-78", "severity": "high",
                    "rule_id": "SEC-SHELL-INJECTION",
                }],
                "category": "risk",
            }
            module = load_import_script()
            store.save_evaluation_case(
                record["id"], record["split"], record["diff"],
                module.to_store_expected(record), "github-real", True,
                record["category"],
            )
            listed = store.list_evaluation_cases("validation", True, 10, "github-real")
            self.assertEqual(1, len(listed))
            self.assertEqual("github-real", listed[0]["source"])
            self.assertEqual("risk", listed[0]["category"])
            # The same record must be invisible on the explicit builtin path.
            self.assertEqual(
                0, len(store.list_evaluation_cases("validation", True, 10, "builtin"))
            )
        finally:
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
