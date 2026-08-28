"""tests/evaluation_v2/test_candidate_freeze.py

Exercise the failure-mining -> declarative-skill synthesis -> safety-gate ->
freeze -> replay protocol (plan phases 5-10).  Candidates are mined *only* from
Validation results and frozen before any blind Holdout measurement.
"""
import sys
import unittest
from os.path import abspath, dirname

ROOT = dirname(dirname(dirname(abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TEST_DIR = dirname(abspath(__file__))
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

from evoagent.evaluation_harness import RULE_TO_CWE  # noqa: E402
from evoagent.evaluation_v2.evolution_protocol import (  # noqa: E402
    FrozenCandidateManifest,
    _clean_needle,
    _longest_common_substring,
    _majority,
    freeze_candidate,
    reviewer_from_manifest,
    safety_gates,
    synthesize_artifact,
)

from helpers import make_risk_case  # noqa: E402


class CandidateFreezeTests(unittest.TestCase):
    def test_longest_common_substring_of_added_lines(self):
        self.assertEqual(" eval(", _longest_common_substring(
            ["result = eval(value)", "return eval(other)"]))
        self.assertEqual("", _longest_common_substring([]))

    def test_clean_needle_guards_against_short_matches(self):
        lines = ["x", "xy", "xyz"]
        self.assertEqual("", _clean_needle(lines))

    def test_majority_picks_dominant_severity(self):
        self.assertEqual("critical", _majority(["high", "critical", "critical"]))

    def test_synthesize_artifact_produces_valid_cwe_rules(self):
        cwe = RULE_TO_CWE["SEC-HARDCODED-SECRET"]
        experiences = [
            {"expected_cwe": cwe, "severity": "high",
             "evidence": "    api_key = \"production-secret-3\"\n"},
            {"expected_cwe": cwe, "severity": "high",
             "evidence": "    token = \"production-secret-3\"\n"},
        ]
        artifact = synthesize_artifact(experiences)
        # validate_artifact normalises the shape; rules must carry cwe rule_ids.
        rules = artifact["rules"]
        self.assertTrue(rules, "confirmed false negatives must yield rules")
        self.assertTrue(all(r["rule_id"] == cwe for r in rules))
        self.assertIn("match", rules[0])
        self.assertIn("production-secret", rules[0]["match"])

    def test_freezing_candidate_is_validation_only(self):
        artifact = synthesize_artifact([
            {"expected_cwe": RULE_TO_CWE["SEC-HARDCODED-SECRET"],
             "severity": "high",
             "evidence": "    api_key = \"production-secret-3\"\n"},
        ])
        gates = {"passed": True, "gates": {}}
        manifest = freeze_candidate(artifact, "frozen-sha", gates)
        self.assertIsInstance(manifest, FrozenCandidateManifest)
        self.assertEqual("validation", manifest.created_from_split)
        self.assertEqual("frozen-sha", manifest.validation_dataset_sha256)
        self.assertEqual("PASS", manifest.gate_result)
        self.assertEqual(manifest.to_dict()["candidate_id"], manifest.candidate_id)

    def test_reviewer_from_manifest_reproduces_findings(self):
        cwe = RULE_TO_CWE["SEC-HARDCODED-SECRET"]
        artifact = synthesize_artifact([
            {"expected_cwe": cwe, "severity": "high",
             "evidence": "    api_key = \"production-secret-3\"\n"},
        ])
        manifest = freeze_candidate(artifact, "sha", {"passed": True, "gates": {}})
        reviewer = reviewer_from_manifest(manifest)
        # The replay must reproduce a finding on the exact mined pattern.
        case = make_risk_case(
            rule_id="SEC-HARDCODED-SECRET", severity="high",
            added_line="    api_key = \"production-secret-3\"\n")
        from evoagent.diff_parser import parse_unified_diff
        findings = reviewer.review(case["diff"], parse_unified_diff(case["diff"]))
        self.assertTrue(findings)
        self.assertEqual(cwe, findings[0].rule_id)

    def test_safety_gates_pass_on_better_evolved(self):
        stable = {"metrics": {
            "detection": {"f1": 0.70, "high_risk_recall": 0.80, "clean_accuracy": 0.90},
            "runtime": {"execution_success_rate": 1.0}},
            "case_results": [{"high_total": 10, "high_hits": 8}]}
        evolved = {"metrics": {
            "detection": {"f1": 0.90, "high_risk_recall": 1.0, "clean_accuracy": 0.90},
            "runtime": {"execution_success_rate": 1.0}},
            "case_results": [{"high_total": 10, "high_hits": 10}]}
        gates = safety_gates(stable, evolved)
        self.assertTrue(gates["passed"])
        self.assertTrue(all(g["passed"] for g in gates["gates"].values()))

    def test_safety_gates_fail_on_regression(self):
        stable = {"metrics": {
            "detection": {"f1": 0.90, "high_risk_recall": 1.0, "clean_accuracy": 0.95},
            "runtime": {"execution_success_rate": 1.0}},
            "case_results": [{"high_total": 10, "high_hits": 10}]}
        evolved = {"metrics": {
            "detection": {"f1": 0.60, "high_risk_recall": 0.50, "clean_accuracy": 0.80},
            "runtime": {"execution_success_rate": 0.9}},
            "case_results": [{"high_total": 10, "high_hits": 5}]}
        gates = safety_gates(stable, evolved)
        self.assertFalse(gates["passed"])


if __name__ == "__main__":
    unittest.main()