"""Work Package 3: Experience bypass and progressive switch.

Covers routing determinism, fingerprint stability/privacy, dedup, corroboration
isolation, Shadow vs Enforce auto_propose, and feedback main-path compatibility.
"""
import os
import tempfile
import unittest

from evoagent import experience as exp_mod
from evoagent.config import Settings
from evoagent.diff_parser import parse_unified_diff
from evoagent.models import ReviewReport, TaskState, TraceEvent
from evoagent.service import ReviewService
from evoagent.skill_evolution import SkillEvolutionEngine
from evoagent.store import TaskStore, utc_now


RISK_DIFF = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+dangerous_call(data)\n"
CLEAN_DIFF = "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+safe_call(data)\n"
FINDING = {
    "rule_id": "SEC-DANGEROUS-CALL", "severity": "high", "path": "a.py", "line": 1,
    "title": "Dangerous call", "explanation": "matches", "fix": "use safe", "test": "add test",
}


def _report():
    return ReviewReport(repository="org/repo", pull_request=1, summary="s", risk="low")


def _event():
    return TraceEvent(1, TaskState.SUCCESS, "done", utc_now())


class ExperienceRoutingTests(unittest.TestCase):
    def test_route_types(self):
        parsed = parse_unified_diff(RISK_DIFF)
        self.assertEqual(exp_mod.RULE_CANDIDATE, exp_mod.route("missed_issue", FINDING, parsed.added_lines)["experience_type"])
        incomplete = {"rule_id": "not-a-rule", "path": "a.py", "line": 1}
        self.assertEqual(exp_mod.SEMANTIC_MEMORY, exp_mod.route("missed_issue", incomplete, parsed.added_lines)["experience_type"])
        self.assertEqual(exp_mod.RULE_REFINEMENT, exp_mod.route("false_positive", FINDING, parsed.added_lines)["experience_type"])
        self.assertEqual(exp_mod.REPAIR_CANDIDATE, exp_mod.route("bad_fix", {}, parsed.added_lines)["experience_type"])
        self.assertEqual(exp_mod.POSITIVE_SIGNAL, exp_mod.route("accepted", {}, parsed.added_lines)["experience_type"])

    def test_incomplete_missed_issue_is_not_rule_candidate(self):
        parsed = parse_unified_diff(RISK_DIFF)
        routed = exp_mod.route("missed_issue", {"rule_id": "SEC-X"}, parsed.added_lines)
        self.assertEqual(exp_mod.SEMANTIC_MEMORY, routed["experience_type"])
        # No evidence on the line -> semantic memory, not a candidate.
        no_line = {"rule_id": "SEC-X", "path": "a.py", "line": 999}
        self.assertEqual(exp_mod.SEMANTIC_MEMORY, exp_mod.route("missed_issue", no_line, parsed.added_lines)["experience_type"])

    def test_fingerprint_excludes_task_id_and_is_stable(self):
        parsed = parse_unified_diff(RISK_DIFF)
        a = exp_mod.build_experience("t1", "org/repo", "task-1", "missed_issue", FINDING, parsed.added_lines)
        b = exp_mod.build_experience("t1", "org/repo", "task-2", "missed_issue", FINDING, parsed.added_lines)
        # Same evidence across tasks -> same aggregate fingerprint.
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        # Fingerprint does not contain the task id.
        self.assertNotIn("task-1", a["fingerprint"])
        # Different tenant or repository -> different fingerprint.
        c = exp_mod.build_experience("t2", "org/repo", "task-1", "missed_issue", FINDING, parsed.added_lines)
        d = exp_mod.build_experience("t1", "other/repo", "task-1", "missed_issue", FINDING, parsed.added_lines)
        self.assertNotEqual(a["fingerprint"], c["fingerprint"])
        self.assertNotEqual(a["fingerprint"], d["fingerprint"])

    def test_evidence_normalization_and_secret_masking(self):
        normalized = exp_mod.normalize_evidence("  password = \"hunter2\"   and  x\n")
        self.assertNotIn("hunter2", normalized)
        self.assertIn("<REDACTED>", normalized)
        # Whitespace collapsed, no over-generalization of variable names.
        self.assertEqual("a + b", exp_mod.normalize_evidence("a +   b"))


class ExperienceStoreTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _seed(self, tenant, repo, task_id, finding=FINDING, diff=RISK_DIFF):
        parsed = parse_unified_diff(diff)
        return exp_mod.build_experience(tenant, repo, task_id, "missed_issue", finding, parsed.added_lines)

    def test_same_task_duplicate_counts_once(self):
        e = self._seed("t1", "org/repo", "task-1")
        first = self.store.record_experience(e["tenant_id"], e["repository"], e["task_id"],
                                             e["source_type"], e["category"], e["experience_type"],
                                             e["fingerprint"], e["payload"], e["evidence"],
                                             e["confidence"], e["status"])
        second = self.store.record_experience(e["tenant_id"], e["repository"], e["task_id"],
                                              e["source_type"], e["category"], e["experience_type"],
                                              e["fingerprint"], e["payload"], e["evidence"],
                                              e["confidence"], e["status"])
        self.assertTrue(first["inserted"])
        self.assertFalse(second["inserted"])
        self.assertEqual(1, len(self.store.list_experiences("t1")))

    def test_corroboration_isolated_by_tenant(self):
        ea = self._seed("tenant-a", "org/repo", "task-1")
        eb = self._seed("tenant-b", "org/repo", "task-1")
        self.store.record_experience(ea["tenant_id"], ea["repository"], ea["task_id"], ea["source_type"],
                                     ea["category"], ea["experience_type"], ea["fingerprint"], ea["payload"],
                                     ea["evidence"], ea["confidence"], ea["status"])
        self.store.record_experience(eb["tenant_id"], eb["repository"], eb["task_id"], eb["source_type"],
                                     eb["category"], eb["experience_type"], eb["fingerprint"], eb["payload"],
                                     eb["evidence"], eb["confidence"], eb["status"])
        # Only tenant-a corroborates; tenant-b stays observed.
        self.store.corroborate_experiences("tenant-a", ea["fingerprint"])
        self.assertEqual("corroborated", self.store.list_experiences("tenant-a")[0]["status"])
        self.assertEqual("observed", self.store.list_experiences("tenant-b")[0]["status"])

    def test_different_tasks_same_evidence_corroborate(self):
        e1 = self._seed("t1", "org/repo", "task-1")
        e2 = self._seed("t1", "org/repo", "task-2")
        self.assertEqual(e1["fingerprint"], e2["fingerprint"])
        for e in (e1, e2):
            self.store.record_experience(e["tenant_id"], e["repository"], e["task_id"],
                                         e["source_type"], e["category"], e["experience_type"],
                                         e["fingerprint"], e["payload"], e["evidence"],
                                         e["confidence"], e["status"])
        self.store.corroborate_experiences("t1", e1["fingerprint"])
        self.assertTrue(all(x["status"] == "corroborated" for x in self.store.list_experiences("t1")))


class ExperienceSwitchTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)
        self.store.save_evaluation_case(
            "danger-validation", "validation", RISK_DIFF,
            [{"path": "a.py", "line": 1, "rule_id": "SEC-DANGEROUS-CALL", "min_severity": "high"}], "test",
        )
        self.store.save_evaluation_case("clean-holdout", "holdout", CLEAN_DIFF, [], "test")

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _engine(self, mode):
        return SkillEvolutionEngine(
            self.store, min_cases=1, max_cases=10, min_improvement=.01, min_holdout_cases=1,
            experience_mode=mode,
        )

    def _make_task(self, task_id, tenant="tenant-a"):
        self.store.create(task_id, "org/repo", 1, {"source": "test"}, tenant)
        self.store.save_task_payload(task_id, RISK_DIFF)

    def _record(self, task_id, tenant="tenant-a"):
        self._make_task(task_id, tenant)
        self.store.record_failure_case(task_id, "missed_issue", {"finding": FINDING})
        e = self._seed(tenant, task_id)
        self.store.record_experience(e["tenant_id"], e["repository"], e["task_id"], e["source_type"],
                                     e["category"], e["experience_type"], e["fingerprint"], e["payload"],
                                     e["evidence"], e["confidence"], e["status"])
        return e

    def _seed(self, tenant, task_id):
        return exp_mod.build_experience(tenant, "org/repo", task_id, "missed_issue", FINDING,
                                        parse_unified_diff(RISK_DIFF).added_lines)

    def test_shadow_auto_propose_reads_failure_cases(self):
        # Shadow mode: a single observed (non-corroborated) feedback still drives
        # evolution because auto_propose reads failure_cases.
        self._record("task-1")
        result = self._engine("shadow").auto_propose("evolved-review", "tenant-a")
        self.assertEqual("activated", result["decision"])

    def test_enforce_ignores_single_observed_feedback(self):
        # Enforce mode: a single (non-corroborated) feedback produces no candidate.
        self._record("task-1")
        result = self._engine("enforce").auto_propose("evolved-review", "tenant-a")
        self.assertEqual("deferred", result["decision"])
        self.assertIsNone(result["version"])

    def test_enforce_consumes_after_activation_and_resolves_failure_cases(self):
        e1 = self._record("task-1")
        self._record("task-2")
        self.store.corroborate_experiences("tenant-a", e1["fingerprint"])
        result = self._engine("enforce").auto_propose("evolved-review", "tenant-a")
        self.assertEqual("activated", result["decision"])
        self.assertEqual("consumed", self.store.list_experiences("tenant-a")[0]["status"])
        self.assertTrue(all(c["resolved"] for c in self.store.list_failure_cases(False, 100, "tenant-a")))


class FeedbackCompatibilityTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _service(self, mode):
        settings = Settings(
            host="127.0.0.1", port=8080, db_path=self.path, max_diff_bytes=10000, max_steps=8,
            timeout_seconds=10, llm_base_url="", llm_api_key="", llm_model="",
            github_webhook_secret="", github_token="", auto_post_review=False,
            skills_dir="skills", experience_mode=mode,
        )
        return ReviewService(settings)

    def test_shadow_feedback_keeps_main_path_and_appends_experience(self):
        service = self._service("shadow")
        service.store.create("task-fb", "org/repo", 1, {}, "tenant-a")
        service.store.save_task_payload("task-fb", RISK_DIFF)
        service.store.succeed("task-fb", _report(), _event())
        try:
            result = service.record_feedback("task-fb", "missed_issue", FINDING, "note", "tenant-a")
            self.assertEqual(True, result["recorded"])
            self.assertEqual("missed_issue", result["category"])
            self.assertIn("experience", result)
            self.assertEqual(exp_mod.RULE_CANDIDATE, result["experience"]["experience_type"])
            self.assertEqual(1, len(service.store.list_failure_cases()))
            self.assertEqual(1, len(service.store.list_experiences("tenant-a")))
        finally:
            service.close()

    def test_default_mode_has_no_experience_side_effect(self):
        service = self._service("off")
        service.store.create("task-fb", "org/repo", 1, {}, "tenant-a")
        service.store.save_task_payload("task-fb", RISK_DIFF)
        service.store.succeed("task-fb", _report(), _event())
        try:
            result = service.record_feedback("task-fb", "missed_issue", FINDING, "note", "tenant-a")
            self.assertEqual({"recorded": True, "category": "missed_issue"}, result)
            self.assertEqual(0, len(service.store.list_experiences("tenant-a")))
        finally:
            service.close()

    def test_shadow_write_failure_does_not_break_feedback(self):
        from unittest import mock
        service = self._service("shadow")
        service.store.create("task-fb", "org/repo", 1, {}, "tenant-a")
        service.store.save_task_payload("task-fb", RISK_DIFF)
        service.store.succeed("task-fb", _report(), _event())
        try:
            with mock.patch.object(
                service.store, "record_experience",
                side_effect=RuntimeError("experience db unavailable"),
            ):
                result = service.record_feedback("task-fb", "missed_issue", FINDING, "note", "tenant-a")
            # Main path must still succeed and the failure is non-fatal.
            self.assertEqual(True, result["recorded"])
            self.assertEqual("missed_issue", result["category"])
            self.assertNotIn("experience", result)
            self.assertEqual(1, len(service.store.list_failure_cases()))
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()