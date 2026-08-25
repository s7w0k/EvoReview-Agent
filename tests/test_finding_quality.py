"""Work Package 6: finding clustering, confidence and result classification.

Covers deterministic duplicate clustering (off/shadow/on), multi-agent
consensus and historical false-positive adjustment, confidence monotonicity,
classification buckets, config validation and the append-only report fields.
"""
import os
import tempfile
import unittest

from evoagent import confidence as conf_mod
from evoagent.agents import finding_key
from evoagent.config import Settings
from evoagent.finding_cluster import cluster_findings
from evoagent.harness import ReviewHarness
from evoagent.models import Finding, ReviewReport, Severity
from evoagent.reviewer import LocalRuleReviewer
from evoagent.service import ReviewService
from evoagent.store import TaskStore


def _finding(rule_id, path="app.py", line=1, severity="high", confidence=0.9):
    return Finding(
        rule_id=rule_id, severity=Severity(severity), title=rule_id,
        explanation="e", path=path, line=line, evidence="evidence",
        fix="f", test="t", confidence=confidence,
    )


class FindingClusteringTests(unittest.TestCase):
    def test_off_and_shadow_keep_duplicates(self):
        findings = [
            _finding("SEC-A", line=2, severity="high", confidence=0.9),
            _finding("SEC-B", line=2, severity="low", confidence=0.9),
        ]
        off, meta_off = cluster_findings(findings, "off")
        shadow, meta_shadow = cluster_findings(findings, "shadow")
        self.assertEqual(2, len(off))
        self.assertEqual(2, len(shadow))
        self.assertEqual([(f.rule_id, f.line) for f in off],
                         [(f.rule_id, f.line) for f in shadow])
        self.assertEqual("off", meta_off["clustering"])
        self.assertEqual("shadow", meta_shadow["clustering"])
        self.assertEqual(1, meta_shadow["clusters"])
        self.assertEqual(1, meta_shadow["duplicates"])

    def test_on_merges_and_keeps_highest_severity_primary(self):
        findings = [
            _finding("SEC-A", line=2, severity="high", confidence=0.5),
            _finding("SEC-B", line=2, severity="low", confidence=0.95),
        ]
        merged, meta = cluster_findings(findings, "on")
        self.assertEqual(1, len(merged))
        self.assertEqual("SEC-A", merged[0].rule_id)  # severity wins over confidence
        self.assertEqual(1, meta["duplicates"])

    def test_different_lines_are_not_merged(self):
        findings = [
            _finding("SEC-A", line=2),
            _finding("SEC-B", line=3),
        ]
        merged, meta = cluster_findings(findings, "on")
        self.assertEqual(2, len(merged))
        self.assertEqual(0, meta["duplicates"])


class ConfidenceTests(unittest.TestCase):
    def test_consensus_improves_confidence_monotonically(self):
        low_consensus = conf_mod.enhance_confidence(0.8, 0.33, 0.0, True)
        high_consensus = conf_mod.enhance_confidence(0.8, 1.0, 0.0, True)
        self.assertGreater(high_consensus, low_consensus)

    def test_historical_fp_rate_reduces_confidence(self):
        no_history = conf_mod.enhance_confidence(0.8, 1.0, 0.0, True)
        bad_history = conf_mod.enhance_confidence(0.8, 1.0, 0.5, True)
        self.assertGreater(no_history, bad_history)

    def test_disabled_keeps_original_confidence(self):
        self.assertEqual(
            0.8, conf_mod.enhance_confidence(0.8, 0.33, 0.9, False)
        )

    def test_apply_enhancement_uses_sources_and_fp_history(self):
        finding = _finding("SEC-A", line=1, confidence=0.9)
        key = finding_key(finding)
        sources = {key: ["security-agent"]}
        cases = [
            {"category": "false_positive", "payload": {"finding": {"rule_id": "SEC-A"}}},
            {"category": "missed_issue", "payload": {"finding": {"rule_id": "SEC-A"}}},
        ]
        enhanced = conf_mod.apply_enhancement(
            [finding], sources, 2, cases, True,
        )
        self.assertLess(enhanced[0].confidence, 0.9)  # 50% FP history + 0.5 consensus

    def test_apply_enhancement_disabled_returns_same_objects(self):
        finding = _finding("SEC-A", confidence=0.9)
        enhanced = conf_mod.apply_enhancement(
            [finding], {}, 1, [], False,
        )
        self.assertIs(finding, enhanced[0])
        self.assertEqual(0.9, enhanced[0].confidence)


class ClassificationTests(unittest.TestCase):
    def test_buckets_assign_confirmed_needs_review_suggestion(self):
        findings = [
            _finding("SEC-C", confidence=0.9),
            _finding("SEC-N", confidence=0.6),
            _finding("SEC-S", confidence=0.3),
        ]
        result = conf_mod.classify(findings, (0.8, 0.5))
        self.assertEqual(["SEC-C"], [i["rule_id"] for i in result["confirmed"]])
        self.assertEqual(["SEC-N"], [i["rule_id"] for i in result["needs_review"]])
        self.assertEqual(["SEC-S"], [i["rule_id"] for i in result["suggestion"]])

    def test_parse_buckets_validates(self):
        self.assertEqual((0.8, 0.5), conf_mod.parse_buckets("0.8,0.5"))
        with self.assertRaises(ValueError):
            conf_mod.parse_buckets("0.5,0.8")   # ascending is rejected
        with self.assertRaises(ValueError):
            conf_mod.parse_buckets("0.8")       # needs two values
        with self.assertRaises(ValueError):
            conf_mod.parse_buckets("1.5,0.5")   # out of range


class ReportCompatibilityTests(unittest.TestCase):
    def test_report_dict_keeps_original_fields_and_adds_metadata(self):
        report = ReviewReport(
            repository="org/repo", pull_request=1, summary="s", risk="high",
            findings=[_finding("SEC-A")],
            classification={"confirmed": [{"rule_id": "SEC-A"}]},
            clustering={"clustering": "shadow", "clusters": 1, "duplicates": 0},
        )
        value = report.to_dict()
        for key in ("repository", "pull_request", "summary", "risk", "findings",
                    "files_reviewed", "reviewer", "collaboration"):
            self.assertIn(key, value)
        self.assertEqual({"confirmed": [{"rule_id": "SEC-A"}]}, value["classification"])
        self.assertEqual("shadow", value["clustering"]["clustering"])

    def test_report_round_trip_preserves_new_fields(self):
        report = ReviewReport(
            repository="org/repo", pull_request=1, summary="s", risk="low",
            findings=[_finding("SEC-A")],
            classification={"confirmed": []},
            clustering={"clustering": "off", "clusters": 0, "duplicates": 0},
        )
        restored = ReviewHarness._report_from_dict(report.to_dict())
        self.assertEqual(report.classification, restored.classification)
        self.assertEqual(report.clustering, restored.clustering)


class HarnessQualityTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_report_carries_classification_and_clustering(self):
        task_id = "quality-task"
        self.store.create(task_id, "demo/repo", 7, {"source": "test"})
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+eval(value)\n"
        harness = ReviewHarness(
            self.store, LocalRuleReviewer(), finding_clustering="on",
        )
        report = harness.run(task_id, "demo/repo", 7, diff)
        self.assertGreaterEqual(len(report.classification.get("confirmed", [])), 1)
        self.assertEqual("on", report.clustering["clustering"])


class QualityServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _service(self, **overrides):
        values = dict(
            host="127.0.0.1", port=8080, db_path=self.path, max_diff_bytes=10000,
            max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="",
            llm_model="", github_webhook_secret="", github_token="",
            auto_post_review=False, skills_dir="skills",
        )
        values.update(overrides)
        return ReviewService(Settings(**values))

    def test_defaults_keep_legacy_behavior(self):
        service = self._service()
        try:
            self.assertEqual("off", service.harness.finding_clustering)
            self.assertFalse(service.harness.confidence_enhance)
        finally:
            service.close()

    def test_enabled_wiring(self):
        service = self._service(finding_clustering="on", confidence_enhance=True)
        try:
            self.assertEqual("on", service.harness.finding_clustering)
            self.assertTrue(service.harness.confidence_enhance)
            self.assertEqual((0.8, 0.5), service.harness.confidence_buckets)
        finally:
            service.close()

    def test_invalid_clustering_fails_at_startup(self):
        with self.assertRaises(ValueError):
            self._service(finding_clustering="aggressive")

    def test_invalid_buckets_fail_at_startup(self):
        with self.assertRaises(ValueError):
            self._service(confidence_buckets="0.5,0.8")


if __name__ == "__main__":
    unittest.main()
