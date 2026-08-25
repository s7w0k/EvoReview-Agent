"""Work Package 4: marginal metrics, usage stats and read-only curator.

Covers shadow/enforce marginal gating, explicit source_skill attribution,
best-effort metric writes, curator duplicate/tighten/stale recommendations,
curator's lack of write capability and tenant isolation.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from evoagent.config import Settings
from evoagent.models import Finding, ReviewReport, Severity
from evoagent.service import ReviewService
from evoagent.skill_curator import (
    DUPLICATE, STALE_CANDIDATE, TIGHTEN_TRIGGER, SkillCurator,
)
from evoagent.skill_evolution import SkillEvolutionEngine
from evoagent.store import TaskStore, utc_now


RISK_DIFF = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+dangerous_call(data)\n"
CLEAN_DIFF = "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+safe_call(data)\n"
RULE = {
    "rule_id": "SEC-DANGEROUS-CALL", "match": "dangerous_call",
    "ignore_case": False, "include_paths": [], "exclude_paths": ["tests/"],
    "title": "Dangerous call", "explanation": "matches", "fix": "use safe",
    "test": "add test", "confidence": 0.85, "severity": "high",
}
EXPECTED = [{"path": "a.py", "line": 1, "rule_id": "SEC-DANGEROUS-CALL", "min_severity": "high"}]


def _candidate_artifact():
    return {"name": "evolved-review", "rules": [dict(RULE)]}


def _finding(source_skill=None, rule_id="SEC-DANGEROUS-CALL"):
    return Finding(
        rule_id=rule_id, severity=Severity.HIGH, title="t", explanation="e",
        path="a.py", line=1, evidence="dangerous_call(data)", fix="f", test="t",
        source_skill=source_skill,
    )


def _report(findings):
    return ReviewReport(
        repository="org/repo", pull_request=1, summary="s", risk="low",
        findings=findings,
    )


def _service_settings(path, **overrides):
    values = dict(
        host="127.0.0.1", port=8080, db_path=path, max_diff_bytes=10000,
        max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="",
        llm_model="", github_webhook_secret="", github_token="",
        auto_post_review=False, skills_dir="skills",
    )
    values.update(overrides)
    return Settings(**values)


class MarginalGateTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)
        self.store.save_evaluation_case(
            "danger-validation", "validation", RISK_DIFF, EXPECTED, "test",
        )
        self.store.save_evaluation_case("clean-holdout", "holdout", CLEAN_DIFF, [], "test")

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _engine(self, marginal_gate="off", min_unique_tp=1):
        return SkillEvolutionEngine(
            self.store, min_cases=1, max_cases=10, min_improvement=.01,
            min_holdout_cases=1, marginal_gate=marginal_gate,
            min_unique_tp=min_unique_tp,
        )

    def test_marginal_gate_off_changes_nothing(self):
        result = self._engine("off").propose("evolved-review", _candidate_artifact(), "tenant-a")
        self.assertEqual("activated", result["decision"])
        self.assertNotIn("marginal_gate", result["gates"])

    def test_shadow_marginal_gate_records_without_changing_decision(self):
        # min_unique_tp=5 is impossible here; shadow must NOT change the decision.
        result = self._engine("shadow", min_unique_tp=5).propose(
            "evolved-review", _candidate_artifact(), "tenant-a",
        )
        self.assertEqual("activated", result["decision"])
        gate = result["gates"]["marginal_gate"]
        self.assertEqual("shadow", gate["mode"])
        self.assertFalse(gate["would_pass"])
        self.assertEqual(1, gate["unique_true_positives"])
        self.assertEqual(0, gate["new_false_positives"])

    def test_enforce_marginal_gate_rejects_only_when_configured(self):
        # Same impossible threshold, but enforce: the candidate is rejected.
        result = self._engine("enforce", min_unique_tp=5).propose(
            "evolved-review", _candidate_artifact(), "tenant-a",
        )
        self.assertEqual("rejected", result["decision"])
        self.assertIn("marginal gate failed", result["reason"])
        gate = result["gates"]["marginal_gate"]
        self.assertEqual("enforce", gate["mode"])
        self.assertFalse(gate["would_pass"])

    def test_enforce_passing_gate_still_activates(self):
        result = self._engine("enforce", min_unique_tp=1).propose(
            "evolved-review", _candidate_artifact(), "tenant-a",
        )
        self.assertEqual("activated", result["decision"])
        self.assertTrue(result["gates"]["marginal_gate"]["would_pass"])

    def test_compute_marginal_counts_new_false_positives(self):
        engine = self._engine()
        baseline = {"name": "evolved-review", "rules": [dict(RULE)]}
        candidate = {
            "name": "evolved-review",
            "rules": [dict(RULE), dict(RULE, rule_id="FP-RULE", match="data")],
        }
        marginal = engine._compute_marginal(
            [{"diff": RISK_DIFF, "expected": EXPECTED}], baseline, candidate,
        )
        self.assertEqual(0, marginal["unique_true_positives"])
        self.assertEqual(1, marginal["new_false_positives"])
        self.assertEqual(1, marginal["finding_overlap"])

    def test_marginal_gate_persisted_in_run_metrics(self):
        self._engine("shadow", min_unique_tp=5).propose(
            "evolved-review", _candidate_artifact(), "tenant-a",
        )
        runs = self.store.list_skill_evolution_runs(10, "tenant-a")
        self.assertEqual(1, len(runs))
        gate = runs[0]["metrics"]["marginal_gate"]
        self.assertEqual("shadow", gate["mode"])


class UsageMetricsTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.settings = _service_settings(self.path)
        self.service = ReviewService(self.settings)
        self.service.store.save_skill_artifact(
            "evolved-review", _candidate_artifact(), 0.9, True, "tenant-a",
        )
        self.reviewer = self.service._active_evolved_reviewers("tenant-a")[0]
        self.assertEqual("evolved-review@1", self.reviewer.name)

    def tearDown(self):
        try:
            self.service.close()
        finally:
            if os.path.exists(self.path):
                os.unlink(self.path)

    def test_attributed_findings_are_counted(self):
        report = _report([_finding("evolved-review@1"), _finding("evolved-review@1")])
        self.service._record_skill_usage("tenant-a", [self.reviewer], report)
        stats = self.service.store.get_skill_usage_stats("tenant-a", "evolved-review", 1)
        self.assertIsNotNone(stats)
        self.assertEqual(1, stats["executions"])
        self.assertEqual(2, stats["findings_proposed"])

    def test_unattributed_findings_are_not_counted(self):
        report = _report([_finding(), _finding()])
        self.service._record_skill_usage("tenant-a", [self.reviewer], report)
        stats = self.service.store.get_skill_usage_stats("tenant-a", "evolved-review", 1)
        self.assertIsNotNone(stats)
        self.assertEqual(0, stats["findings_proposed"])

    def test_metrics_write_failure_does_not_break_review_report(self):
        from unittest import mock

        report = _report([_finding("evolved-review@1")])
        with mock.patch.object(
            self.service.store, "record_skill_usage",
            side_effect=RuntimeError("metrics db unavailable"),
        ):
            self.service._record_skill_usage("tenant-a", [self.reviewer], report)
        # The report itself is untouched and the call did not raise.
        self.assertEqual(1, len(report.findings))

    def test_feedback_counters_update_usage_stats(self):
        self.service.store.create("task-ok", "org/repo", 1, {}, "tenant-a")
        self.service.store.save_task_payload("task-ok", RISK_DIFF)
        self.service.store.succeed("task-ok", _report([]), self._event())
        attributed = {"rule_id": "SEC-DANGEROUS-CALL", "path": "a.py", "line": 1,
                      "severity": "high", "source_skill": "evolved-review@1"}
        self.service.record_feedback("task-ok", "accepted", attributed, "ok", "tenant-a")
        self.service.record_feedback(
            "task-ok", "false_positive", attributed, "no", "tenant-a",
        )
        stats = self.service.store.get_skill_usage_stats("tenant-a", "evolved-review", 1)
        self.assertEqual(1, stats["findings_approved"])
        self.assertEqual(1, stats["false_positive_feedback"])

    def test_feedback_without_source_skill_is_not_counted(self):
        self.service.store.create("task-ok2", "org/repo", 1, {}, "tenant-a")
        self.service.store.save_task_payload("task-ok2", RISK_DIFF)
        self.service.store.succeed("task-ok2", _report([]), self._event())
        unattributed = {"rule_id": "SEC-DANGEROUS-CALL", "path": "a.py", "line": 1}
        self.service.record_feedback("task-ok2", "accepted", unattributed, "ok", "tenant-a")
        self.assertIsNone(
            self.service.store.get_skill_usage_stats("tenant-a", "evolved-review", 1)
        )

    @staticmethod
    def _event():
        from evoagent.models import TaskState, TraceEvent
        return TraceEvent(1, TaskState.SUCCESS, "done", utc_now())


class CuratorTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _duplicate_artifact(self):
        rules = [
            dict(RULE, rule_id="SEC-A"),
            dict(RULE, rule_id="SEC-B"),
        ]
        return {"name": "evolved-review", "rules": rules}

    def test_duplicate_rule_detection(self):
        self.store.save_skill_artifact(
            "evolved-review", self._duplicate_artifact(), 0.9, True, "tenant-a",
        )
        recommendations = SkillCurator(min_samples=1, stale_days=1).recommend(
            self.store, "tenant-a",
        )
        duplicates = [item for item in recommendations if item["type"] == DUPLICATE]
        self.assertEqual(1, len(duplicates))
        self.assertEqual(["SEC-A", "SEC-B"], duplicates[0]["rule_ids"])

    def test_curator_cannot_modify_skill_state(self):
        self.store.save_skill_artifact(
            "evolved-review", self._duplicate_artifact(), 0.9, True, "tenant-a",
        )
        before = self.store.list_audit("tenant-a")
        SkillCurator(min_samples=1, stale_days=1).recommend(self.store, "tenant-a")
        active = self.store.get_active_skill_artifact("evolved-review", "tenant-a")
        self.assertEqual(1, active["version"])
        self.assertTrue(active["active"])
        self.assertEqual("active", active["status"])
        # No writes: no audit entries, no usage rows were created.
        self.assertEqual(len(before), len(self.store.list_audit("tenant-a")))
        self.assertEqual([], self.store.list_skill_usage_stats("tenant-a"))

    def test_tighten_trigger_recommendation(self):
        self.store.save_skill_artifact(
            "evolved-review", _candidate_artifact(), 0.9, True, "tenant-a",
        )
        self.store.record_skill_usage(
            "tenant-a", "evolved-review", 1,
            findings_proposed=4, false_positive_feedback=2,
        )
        recommendations = SkillCurator(min_samples=2, stale_days=30).recommend(
            self.store, "tenant-a",
        )
        tighten = [item for item in recommendations if item["type"] == TIGHTEN_TRIGGER]
        self.assertEqual(1, len(tighten))
        self.assertEqual(2, tighten[0]["false_positive_feedback"])

    def test_below_threshold_is_not_tightened(self):
        self.store.save_skill_artifact(
            "evolved-review", _candidate_artifact(), 0.9, True, "tenant-a",
        )
        self.store.record_skill_usage(
            "tenant-a", "evolved-review", 1,
            findings_proposed=100, false_positive_feedback=1,
        )
        recommendations = SkillCurator(min_samples=2, stale_days=30).recommend(
            self.store, "tenant-a",
        )
        self.assertNotIn(
            TIGHTEN_TRIGGER, [item["type"] for item in recommendations]
        )

    def test_stale_candidate_by_last_used_at(self):
        self.store.save_skill_artifact(
            "evolved-review", _candidate_artifact(), 0.9, True, "tenant-a",
        )
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        self.store.record_skill_usage(
            "tenant-a", "evolved-review", 1, executions=1, last_used_at=old,
        )
        recommendations = SkillCurator(min_samples=2, stale_days=30).recommend(
            self.store, "tenant-a",
        )
        stale = [item for item in recommendations if item["type"] == STALE_CANDIDATE]
        self.assertEqual(1, len(stale))
        self.assertEqual("evolved-review", stale[0]["skill_name"])

    def test_recommendations_are_isolated_by_tenant(self):
        self.store.save_skill_artifact(
            "evolved-review", self._duplicate_artifact(), 0.9, True, "tenant-a",
        )
        self.store.save_skill_artifact(
            "evolved-review", self._duplicate_artifact(), 0.9, True, "tenant-b",
        )
        self.store.record_skill_usage(
            "tenant-a", "evolved-review", 1,
            findings_proposed=4, false_positive_feedback=2,
        )
        recommendations_a = SkillCurator(min_samples=2, stale_days=30).recommend(
            self.store, "tenant-a",
        )
        self.assertTrue(all(item["tenant_id"] == "tenant-a" for item in recommendations_a))
        self.assertIn(DUPLICATE, [item["type"] for item in recommendations_a])
        self.assertIn(TIGHTEN_TRIGGER, [item["type"] for item in recommendations_a])
        # tenant-b has no usage and no recommendations of its own surfaced to A.
        self.assertEqual([], self.store.list_skill_usage_stats("tenant-b"))
        recommendations_b = SkillCurator(min_samples=2, stale_days=30).recommend(
            self.store, "tenant-b",
        )
        self.assertTrue(all(item["tenant_id"] == "tenant-b" for item in recommendations_b))

    def test_curator_disabled_returns_empty_recommendations(self):
        settings = _service_settings(self.path, curator_enabled=False)
        service = ReviewService(settings)
        try:
            self.assertEqual([], service.curator_recommendations("tenant-a"))
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
