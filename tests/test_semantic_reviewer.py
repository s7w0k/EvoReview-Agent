"""Work Package 5: semantic analysis layer.

Covers the stdlib AST analyzer (taint into exec/subprocess, shell=True,
unclosed resources, snapshot fallback), the SemanticReviewer wiring, the dark
switch in ReviewService/config, and graceful fallback for missing external
tools.
"""
import os
import tempfile
import unittest
from unittest import mock

from evoagent import ast_analysis
from evoagent.config import Settings
from evoagent.diff_parser import parse_unified_diff
from evoagent.harness import ReviewHarness
from evoagent.models import Finding, Severity
from evoagent.semantic_reviewer import (
    CompositeSemanticReviewer, SemanticReviewer, build_semantic_reviewer,
)
from evoagent.service import ReviewService


def _diff(*added):
    hunk = "@@ -1 +1,%d @@\n-old\n" % len(added)
    body = "".join("\n+%s" % item for item in added)
    return "--- a/app.py\n+++ b/app.py\n" + hunk + body + "\n"


def _reviewer(analyzer="ast"):
    parsed = parse_unified_diff(_diff(
        "def process(data):", "    cmd = data", "    eval(cmd)",
    ))
    return SemanticReviewer(analyzer), _diff(
        "def process(data):", "    cmd = data", "    eval(cmd)",
    ), parsed


class AstAnalyzerTests(unittest.TestCase):
    def test_tainted_variable_into_eval(self):
        parsed = parse_unified_diff(_diff(
            "def process(data):", "    cmd = data", "    eval(cmd)",
        ))
        findings = ast_analysis.analyze_added_lines(parsed.added_lines)
        self.assertEqual(1, len(findings))
        self.assertEqual("SEM-TAINTED-EXEC", findings[0]["rule_id"])
        self.assertEqual("app.py", findings[0]["path"])
        # eval(cmd) is the third added line; the parser numbers added lines 2..4.
        self.assertEqual(4, findings[0]["line"])
        self.assertEqual("ast", findings[0]["analyzer"])

    def test_direct_eval_of_taint_named_argument(self):
        parsed = parse_unified_diff(_diff("def go():", "    eval(user_input)"))
        findings = ast_analysis.analyze_added_lines(parsed.added_lines)
        self.assertTrue(any(
            f["rule_id"] == "SEM-TAINTED-EXEC" for f in findings
        ))

    def test_subprocess_concatenation_is_flagged(self):
        parsed = parse_unified_diff(_diff(
            "import subprocess", 'subprocess.run("ls " + path)',
        ))
        findings = ast_analysis.analyze_added_lines(parsed.added_lines)
        self.assertTrue(any(
            f["rule_id"] == "SEM-TAINTED-SUBPROCESS" for f in findings
        ))

    def test_shell_true_with_variable_command(self):
        parsed = parse_unified_diff(_diff(
            "import subprocess", "subprocess.Popen(cmd, shell=True)",
        ))
        findings = ast_analysis.analyze_added_lines(parsed.added_lines)
        self.assertTrue(any(
            f["rule_id"] == "SEM-SHELL-INJECTION" for f in findings
        ))

    def test_unclosed_resource(self):
        parsed = parse_unified_diff(_diff(
            "def read(path):", "    f = open(path)", "    return f.read()",
        ))
        findings = ast_analysis.analyze_added_lines(parsed.added_lines)
        self.assertTrue(any(
            f["rule_id"] == "SEM-UNCLOSED-RESOURCE" for f in findings
        ))

    def test_closed_resource_is_not_flagged(self):
        parsed = parse_unified_diff(_diff(
            "def read(path):", "    with open(path) as f:",
            "        return f.read()",
        ))
        findings = ast_analysis.analyze_added_lines(parsed.added_lines)
        self.assertFalse(any(
            f["rule_id"] == "SEM-UNCLOSED-RESOURCE" for f in findings
        ))

    def test_clean_code_produces_no_findings(self):
        parsed = parse_unified_diff(_diff(
            "def safe(value):", "    return str(value)",
        ))
        self.assertEqual([], ast_analysis.analyze_added_lines(parsed.added_lines))

    def test_snapshot_fallback_dedents_single_statement(self):
        # A function body without its header cannot be parsed as a snapshot;
        # the per-line fallback must still catch the tainted exec.
        parsed = parse_unified_diff(_diff("    eval(user_input)"))
        findings = ast_analysis.analyze_added_lines(parsed.added_lines)
        self.assertTrue(any(
            f["rule_id"] == "SEM-TAINTED-EXEC" for f in findings
        ))


class SemanticReviewerTests(unittest.TestCase):
    def test_reviewer_returns_findings_with_analyzer(self):
        reviewer, diff, parsed = _reviewer()
        findings = reviewer.review(diff, parsed)
        self.assertTrue(findings)
        self.assertTrue(all(item.analyzer == "ast" for item in findings))
        self.assertTrue(all(isinstance(item, Finding) for item in findings))

    def test_composite_reviewer_includes_ast_layer(self):
        reviewer = CompositeSemanticReviewer()
        diff = _diff("def process(data):", "    eval(data)")
        parsed = parse_unified_diff(diff)
        findings = reviewer.review(diff, parsed)
        self.assertTrue(any(
            item.rule_id == "SEM-TAINTED-EXEC" for item in findings
        ))

    def test_build_off_returns_none(self):
        self.assertIsNone(build_semantic_reviewer("off"))

    def test_build_ast_returns_semantic_reviewer(self):
        self.assertIsInstance(build_semantic_reviewer("ast"), SemanticReviewer)

    def test_missing_external_tool_falls_back_to_ast(self):
        with mock.patch("evoagent.semantic_reviewer.is_available", return_value=False):
            reviewer = build_semantic_reviewer("bandit")
        self.assertIsInstance(reviewer, SemanticReviewer)

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            build_semantic_reviewer("aggressive")

    def test_analyzer_field_round_trips_through_harness(self):
        finding = Finding(
            rule_id="SEM-TAINTED-EXEC", severity=Severity.HIGH, title="t",
            explanation="e", path="app.py", line=2, evidence="x", fix="f",
            test="t", analyzer="ast",
        )
        restored = ReviewHarness._finding_from_dict(finding.to_dict())
        self.assertEqual("ast", restored.analyzer)
        self.assertEqual("SEM-TAINTED-EXEC", restored.rule_id)


class SemanticReviewerServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _service(self, static_analyzer):
        settings = Settings(
            host="127.0.0.1", port=8080, db_path=self.path, max_diff_bytes=10000,
            max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="",
            llm_model="", github_webhook_secret="", github_token="",
            auto_post_review=False, skills_dir="skills",
            static_analyzer=static_analyzer,
        )
        return ReviewService(settings)

    def test_default_does_not_register_semantic_reviewer(self):
        service = self._service("off")
        try:
            names = {item["name"] for item in service.registry.list()}
            self.assertNotIn("semantic-review", names)
        finally:
            service.close()

    def test_enabled_registers_semantic_reviewer(self):
        service = self._service("ast")
        try:
            names = {item["name"] for item in service.registry.list()}
            self.assertIn("semantic-review", names)
        finally:
            service.close()

    def test_invalid_config_fails_at_startup(self):
        with self.assertRaises(ValueError):
            self._service("aggressive")


if __name__ == "__main__":
    unittest.main()
