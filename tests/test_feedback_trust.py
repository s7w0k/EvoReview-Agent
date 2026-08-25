"""Work Package 9: feedback trust and overfitting protection.

Covers the plan's acceptance items:
- low-trust feedback does not directly generate a candidate when trust is on;
- a missed_issue needs the configured number of independent confirmers;
- candidates compare against recent validated history (enforce gates, shadow
  only records) and cooldown skips a recently rejected fingerprint;
- holdout rotation archives samples without exposing details and keeps audit;
- every default preserves the pre-WP9 behavior exactly.
"""
import os
import tempfile
import unittest
from unittest import mock

from evoagent.config import Settings
from evoagent.models import ReviewReport, TaskState, TraceEvent
from evoagent.service import ReviewService
from evoagent.skill_evolution import SkillEvolutionEngine, validate_artifact
from evoagent.store import TaskStore, utc_now


RISK_DIFF = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+dangerous_call(data)\n"
CLEAN_DIFF = "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+safe_call(data)\n"
YAML_DIFF = "--- a/c.py\n+++ b/c.py\n@@ -1 +1 @@\n-old\n+yaml.load(data)\n"
FINDING = {
    "rule_id": "SEC-DANGEROUS-CALL", "severity": "high", "path": "a.py", "line": 1,
    "title": "Dangerous call", "explanation": "matches", "fix": "use safe", "test": "add test",
    "evidence": "dangerous_call(data)",
}
FINDING_EVIDENCE = {"rule_id": "SEC-DANGEROUS-CALL", "evidence": "dangerous_call(data)"}


def _report():
    return ReviewReport(repository="org/repo", pull_request=1, summary="s", risk="low")


def _event():
    return TraceEvent(1, TaskState.SUCCESS, "done", utc_now())


def _artifact(rules):
    return validate_artifact({"name": "evolved-review", "rules": rules}, "evolved-review")


def _rule(rule_id, match, severity="high"):
    return {
        "rule_id": rule_id, "severity": severity, "match": match,
        "title": rule_id, "explanation": "matches %s" % match,
        "fix": "fix", "test": "test",
    }


class FeedbackTrustEngineTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)
        self.store.create("task-1", "org/a", 1, {}, "default")
        self.store.create("task-2", "org/a", 2, {}, "default")

    def tearDown(self):
        os.unlink(self.path)

    def seed_cases(self):
        self.store.save_evaluation_case(
            "risk-validation", "validation", RISK_DIFF,
            [{"path": "a.py", "line": 1, "rule_id": "SEC-DANGEROUS-CALL",
              "min_severity": "high"}], "test",
        )
        self.store.save_evaluation_case("clean-holdout", "holdout", CLEAN_DIFF, [], "test")

    def engine(self, **kwargs):
        options = {"min_cases": 1, "min_holdout_cases": 1, "max_cases": 10}
        options.update(kwargs)
        return SkillEvolutionEngine(self.store, **options)

    def test_trust_disabled_preserves_legacy_behavior(self):
        # Default: even a never-trusted feedbacker's missed_issue generates a candidate.
        self.store.record_failure_case("task-1", "missed_issue", {
            "finding": FINDING, "feedbacker": "robot-x",
        })
        self.seed_cases()
        result = self.engine().auto_propose("evolved-review", "default")
        self.assertEqual(["SEC-DANGEROUS-CALL"], result["learned_rule_ids"])

    def test_low_trust_feedback_is_downgraded(self):
        # Only a low-trust feedbacker (no accepted history) reports a missed issue.
        self.store.record_failure_case("task-1", "missed_issue", {
            "finding": FINDING, "feedbacker": "robot-x",
        })
        self.seed_cases()
        result = self.engine(trust_enabled=True, trust_min_ratio=0.5).auto_propose(
            "evolved-review", "default",
        )
        self.assertEqual("deferred", result["decision"])
        self.assertEqual([], result["learned_rule_ids"])
        self.assertEqual(0, result["failure_cases_used"])

    def test_high_trust_feedback_generates_candidate(self):
        # The same missed issue from a feedbacker with a 1.0 accepted ratio is used.
        self.store.record_failure_case("task-1", "accepted", {"feedbacker": "human-a"})
        self.store.record_failure_case("task-1", "missed_issue", {
            "finding": FINDING, "feedbacker": "human-a",
        })
        self.seed_cases()
        result = self.engine(trust_enabled=True, trust_min_ratio=0.5).auto_propose(
            "evolved-review", "default",
        )
        self.assertEqual(["SEC-DANGEROUS-CALL"], result["learned_rule_ids"])

    def test_min_confirmers_requires_independent_tasks(self):
        self.store.record_failure_case("task-1", "missed_issue", {"finding": FINDING})
        self.seed_cases()
        single = self.engine(min_confirmers=2).auto_propose("evolved-review", "default")
        self.assertEqual("deferred", single["decision"])
        self.assertEqual([], single["learned_rule_ids"])

        # A second, independent task confirms the same signature.
        self.store.create("task-3", "org/a", 3, {}, "default")
        self.store.record_failure_case("task-3", "missed_issue", {"finding": FINDING})
        confirmed = self.engine(min_confirmers=2).auto_propose("evolved-review", "default")
        self.assertEqual(["SEC-DANGEROUS-CALL"], confirmed["learned_rule_ids"])

    def test_min_confirmers_default_one_preserves_legacy(self):
        self.store.record_failure_case("task-1", "missed_issue", {"finding": FINDING})
        self.seed_cases()
        result = self.engine().auto_propose("evolved-review", "default")
        self.assertEqual(["SEC-DANGEROUS-CALL"], result["learned_rule_ids"])

    def test_cooldown_skips_same_fingerprint_candidate(self):
        self.seed_cases()
        engine = self.engine(cooldown_minutes=30)
        first = engine.propose("evolved-review", _artifact([_rule("SEC-DANGEROUS-CALL", "dangerous_call(data)")]))
        self.assertEqual("activated", first["decision"])
        regressing = engine.propose("evolved-review", _artifact([]))
        self.assertEqual("rejected", regressing["decision"])
        # Same candidate fingerprint rejected moments ago -> cooldown defers.
        again = engine.propose("evolved-review", _artifact([]))
        self.assertEqual("deferred", again["decision"])
        self.assertIn("cooldown", again["reason"])
        self.assertEqual(30, again.get("cooldown_minutes"))

    def test_cooldown_disabled_by_default(self):
        self.seed_cases()
        engine = self.engine()
        self.assertEqual(0, engine.cooldown_minutes)
        self.assertTrue(engine.propose(
            "evolved-review", _artifact([_rule("SEC-DANGEROUS-CALL", "dangerous_call(data)")])
        )["decision"] in {"activated", "rejected"})

    def test_holdout_rotation_archives_oldest_samples_and_keeps_audit(self):
        self.store.save_evaluation_case(
            "risk-validation", "validation", RISK_DIFF,
            [{"path": "a.py", "line": 1, "rule_id": "SEC-DANGEROUS-CALL",
              "min_severity": "high"}], "test",
        )
        self.store.save_evaluation_case(
            "yaml-validation", "validation", YAML_DIFF,
            [{"path": "c.py", "line": 1, "rule_id": "SEC-YAML",
              "min_severity": "high"}], "test",
        )
        for number in range(1, 4):
            self.store.save_evaluation_case(
                "holdout-%d" % number, "holdout", CLEAN_DIFF, [], "test",
            )
        engine = self.engine(holdout_rotation=2)
        # First activation: no rotation yet (1 % 2 != 0).
        engine.propose("evolved-review", _artifact([_rule("SEC-DANGEROUS-CALL", "dangerous_call(data)")]))
        # Second activation triggers rotation of the 2 oldest holdout samples.
        second = engine.propose("evolved-review", _artifact([
            _rule("SEC-DANGEROUS-CALL", "dangerous_call(data)"),
            _rule("SEC-YAML", "yaml.load(data)"),
        ]))
        self.assertEqual("activated", second["decision"])
        run = self.store.list_skill_evolution_runs()[0]
        self.assertEqual(2, len(run["metrics"]["holdout_rotation_archived"]))
        # Active holdout shrinks; full audit history is preserved.
        self.assertEqual(1, len(self.store.list_evaluation_cases("holdout", True, 10)))
        self.assertEqual(3, len(self.store.list_evaluation_cases("holdout", False, 10)))

    def test_provenance_has_wp9_fingerprint_keys(self):
        self.seed_cases()
        engine = self.engine()
        engine.propose("evolved-review", _artifact([_rule("SEC-DANGEROUS-CALL", "dangerous_call(data)")]))
        active = self.store.get_active_skill_artifact("evolved-review")
        provenance = active["provenance"]
        for key in ("model", "tool_version", "dataset_source", "prompt_fingerprint"):
            self.assertIn(key, provenance, key)
        self.assertEqual("declarative", provenance["model"])
        self.assertEqual("builtin", provenance["dataset_source"])


class HistoryComparisonTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)
        self.store.save_evaluation_case(
            "risk-validation", "validation", RISK_DIFF,
            [{"path": "a.py", "line": 1, "rule_id": "SEC-DANGEROUS-CALL",
              "min_severity": "high"}], "test",
        )
        self.store.save_evaluation_case("clean-holdout", "holdout", CLEAN_DIFF, [], "test")

    def tearDown(self):
        os.unlink(self.path)

    def build_history(self):
        # Manually activate two versions so the older one becomes validated
        # without the engine's own monotonicity gate interfering.
        v1 = _artifact([_rule("SEC-DANGEROUS-CALL", "dangerous_call(data)")])
        v2 = _artifact([_rule("SEC-YAML", "yaml.load(data)")])
        self.store.save_skill_artifact("evolved-review", v1, 0.9, True, "default")
        self.store.save_skill_artifact("evolved-review", v2, 0.8, True, "default")
        return v1, v2

    def engine(self, **kwargs):
        options = {"min_cases": 1, "min_holdout_cases": 1, "max_cases": 10}
        options.update(kwargs)
        return SkillEvolutionEngine(self.store, **options)

    def test_default_compare_history_one_has_no_history_gate(self):
        self.build_history()
        engine = self.engine()
        self.assertEqual(1, engine.compare_history)
        candidate = _artifact([_rule("SEC-DANGEROUS-CALL", "dangerous_call(data)")])
        result = engine.propose("evolved-review", candidate)
        run = self.store.list_skill_evolution_runs()[0]
        self.assertIsNone(run["metrics"]["gates"].get("history_non_regression"))
        self.assertEqual([], run["metrics"]["history_comparison"])
        self.assertNotEqual("rejected", result["decision"])

    def test_shadow_only_records_history_comparison(self):
        self.build_history()
        candidate = _artifact([
            _rule("SEC-DANGEROUS-CALL", "dangerous_call(data)"),
            _rule("SEC-YAML", "yaml.load(data)"),
        ])
        engine = self.engine(compare_history=2, marginal_gate="shadow")
        # Force a history regression while the main (active) gates pass.
        with mock.patch.object(
            SkillEvolutionEngine, "_non_regressing",
            side_effect=[True, True, False, False],
        ):
            result = engine.propose("evolved-review", candidate)
        self.assertEqual("activated", result["decision"])
        run = self.store.list_skill_evolution_runs()[0]
        self.assertFalse(run["metrics"]["gates"]["history_non_regression"])
        self.assertEqual(1, len(run["metrics"]["history_comparison"]))
        self.assertIn("score", run["metrics"]["history_comparison"][0])

    def test_enforce_blocks_candidate_regressing_against_history(self):
        self.build_history()
        candidate = _artifact([
            _rule("SEC-DANGEROUS-CALL", "dangerous_call(data)"),
            _rule("SEC-YAML", "yaml.load(data)"),
        ])
        engine = self.engine(compare_history=2, marginal_gate="enforce")
        with mock.patch.object(
            SkillEvolutionEngine, "_non_regressing",
            side_effect=[True, True, False, False],
        ):
            result = engine.propose("evolved-review", candidate)
        self.assertEqual("rejected", result["decision"])
        self.assertIn("regressed", result["reason"])


class ServiceCorroborationTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _service(self, min_confirmers):
        settings = Settings(
            host="127.0.0.1", port=8080, db_path=self.path, max_diff_bytes=10000,
            max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="",
            llm_model="", github_webhook_secret="", github_token="",
            auto_post_review=False, skills_dir="skills", experience_mode="enforce",
            evolution_min_evidence=2, evolution_min_distinct_tasks=2,
            feedback_min_confirmers=min_confirmers,
        )
        return ReviewService(settings)

    def _completed_task(self, service, task_id):
        service.store.create(task_id, "org/repo", 1, {}, "tenant-a")
        service.store.save_task_payload(task_id, RISK_DIFF)
        service.store.succeed(task_id, _report(), _event())

    def test_more_confirmers_required_before_corroboration(self):
        service = self._service(min_confirmers=3)
        try:
            for number in (1, 2):
                task_id = "task-fb-%d" % number
                self._completed_task(service, task_id)
                service.record_feedback(task_id, "missed_issue", FINDING, "note", "tenant-a")
            # 2 distinct confirmers < min_confirmers=3 -> stays observed.
            experiences = service.store.list_experiences("tenant-a")
            self.assertEqual(2, len(experiences))
            self.assertTrue(all(item["status"] == "observed" for item in experiences))
            self.assertEqual(
                0, len(service.store.list_corroborated_rule_candidates("tenant-a"))
            )
            # 3rd independent task crosses the bar and corroborates the
            # fingerprint (every matching experience row flips to corroborated).
            self._completed_task(service, "task-fb-3")
            service.record_feedback("task-fb-3", "missed_issue", FINDING, "note", "tenant-a")
            corroborated = service.store.list_corroborated_rule_candidates("tenant-a")
            self.assertEqual(3, len(corroborated))
            self.assertEqual(1, len({item["fingerprint"] for item in corroborated}))
        finally:
            service.close()


class ConfigValidationTests(unittest.TestCase):
    def test_wp9_env_parsing_and_validation(self):
        os.environ.update({
            "EVOAGENT_FEEDBACK_MIN_CONFIRMERS": "3",
            "EVOAGENT_FEEDBACK_TRUST_ENABLED": "on",
            "EVOAGENT_FEEDBACK_TRUST_MIN_ACCEPTED_RATIO": "0.7",
            "EVOAGENT_EVOLUTION_COMPARE_HISTORY": "3",
            "EVOAGENT_EVOLUTION_COOLDOWN_MINUTES": "15",
            "EVOAGENT_HOLDOUT_ROTATION": "5",
        })
        try:
            settings = Settings.from_env()
            self.assertEqual(3, settings.feedback_min_confirmers)
            self.assertTrue(settings.feedback_trust_enabled)
            self.assertAlmostEqual(0.7, settings.feedback_trust_min_accepted_ratio)
            self.assertEqual(3, settings.evolution_compare_history)
            self.assertEqual(15, settings.evolution_cooldown_minutes)
            self.assertEqual(5, settings.holdout_rotation)
            settings.validate_evolution()
        finally:
            for key in os.environ:
                if key.startswith("EVOAGENT_FEEDBACK_") or key in {
                    "EVOAGENT_EVOLUTION_COMPARE_HISTORY",
                    "EVOAGENT_EVOLUTION_COOLDOWN_MINUTES",
                    "EVOAGENT_HOLDOUT_ROTATION",
                }:
                    os.environ.pop(key, None)

    def test_invalid_trust_ratio_rejected(self):
        os.environ["EVOAGENT_FEEDBACK_TRUST_MIN_ACCEPTED_RATIO"] = "1.5"
        try:
            with self.assertRaisesRegex(ValueError, "ACCEPTED_RATIO"):
                Settings.from_env().validate_evolution()
        finally:
            os.environ.pop("EVOAGENT_FEEDBACK_TRUST_MIN_ACCEPTED_RATIO", None)


if __name__ == "__main__":
    unittest.main()
