"""Work Package 7: format-preserving AST repairs.

Covers comment preservation (the key improvement over the legacy unparse
rewrite), re-verification after repair, line/file limits, unsupported-rule
passthrough, the dark switch and the SafeFixer wiring.
"""
import unittest

from evoagent.ast_fixer import PreservingAstFixer
from evoagent.fixer import SafeFixer


def _finding(path, line, rule_id):
    return {"path": path, "line": line, "rule_id": rule_id}


class PreservingAstFixerTests(unittest.TestCase):
    def test_hardcoded_secret_repair_keeps_comments(self):
        content = (
            "# this comment must survive\n"
            "password = \"secret-value\"\n"
            "print(result)\n"
        )
        findings = [
            _finding("app.py", 2, "SEC-HARDCODED-SECRET"),
            _finding("app.py", 3, "REL-DEBUG-PRINT"),
        ]
        result = PreservingAstFixer().apply(content, findings, "app.py")
        self.assertEqual(["REL-DEBUG-PRINT", "SEC-HARDCODED-SECRET"], result["rules"])
        self.assertIn("# this comment must survive", result["content"])
        self.assertIn('password = os.environ["PASSWORD"]', result["content"])
        self.assertNotIn("print(result)", result["content"])
        self.assertIn("import os", result["content"])

    def test_shell_true_becomes_false_with_format_kept(self):
        content = "def run(cmd):\n    # keep this\n    subprocess.run(cmd, shell=True)\n"
        findings = [_finding("app.py", 3, "SEC-SUBPROCESS-SHELL")]
        result = PreservingAstFixer().apply(content, findings, "app.py")
        self.assertEqual(["SEC-SUBPROCESS-SHELL"], result["rules"])
        self.assertIn("shell=False", result["content"])
        self.assertIn("# keep this", result["content"])
        self.assertNotIn("shell=True", result["content"])

    def test_repair_removes_the_finding_on_recheck(self):
        content = 'token = "abc123-secret"\n'
        findings = [_finding("app.py", 1, "SEC-HARDCODED-SECRET")]
        result = PreservingAstFixer().apply(content, findings, "app.py")
        # The hardcoded-secret regex must no longer match the repaired line.
        from evoagent.reviewer import LocalRuleReviewer
        pattern = next(
            rule[2] for rule in LocalRuleReviewer.RULES
            if rule[0] == "SEC-HARDCODED-SECRET"
        )
        self.assertFalse(pattern.search(result["content"]))

    def test_unsupported_rule_is_left_untouched(self):
        content = "result = eval(user_input)\n"
        findings = [_finding("app.py", 1, "SEC-EVAL")]
        result = PreservingAstFixer().apply(content, findings, "app.py")
        self.assertEqual([], result["rules"])
        self.assertEqual(content, result["content"])

    def test_non_python_file_is_not_modified(self):
        content = "password = 'secret'\n"
        findings = [_finding("app.js", 1, "SEC-HARDCODED-SECRET")]
        result = PreservingAstFixer().apply(content, findings, "app.js")
        self.assertEqual([], result["rules"])
        self.assertEqual(content, result["content"])

    def test_max_lines_rejects_large_repairs(self):
        content = "\n".join(
            "print('debug-%d')" % index for index in range(5)
        ) + "\n"
        findings = [
            _finding("app.py", index + 1, "REL-DEBUG-PRINT")
            for index in range(5)
        ]
        result = PreservingAstFixer(max_lines=2).apply(content, findings, "app.py")
        self.assertIsNotNone(result["rejected_reason"])
        self.assertIn("AST_FIX_MAX_LINES", result["rejected_reason"])

    def test_broken_source_is_rejected(self):
        result = PreservingAstFixer().apply(
            "def broken(:\n", [_finding("app.py", 1, "REL-DEBUG-PRINT")], "app.py",
        )
        self.assertIsNotNone(result["rejected_reason"])


class SafeFixerSwitchTests(unittest.TestCase):
    CONTENT = (
        "# keep this comment\n"
        'password = "hunter2"\n'
        'print("debug")\n'
    )
    FINDINGS = [
        _finding("app.py", 2, "SEC-HARDCODED-SECRET"),
        _finding("app.py", 3, "REL-DEBUG-PRINT"),
    ]

    def test_off_matches_legacy_repair_rules(self):
        fixer = SafeFixer(ast_fixer_enabled=False)
        result = fixer.apply(self.CONTENT, self.FINDINGS, "app.py")
        self.assertIn("SEC-HARDCODED-SECRET", result["rules"])
        self.assertIn("REL-DEBUG-PRINT", result["rules"])
        # Legacy unparse-based rewrite drops comments.
        self.assertNotIn("# keep this comment", result["content"])

    def test_on_preserves_comments(self):
        fixer = SafeFixer(ast_fixer_enabled=True)
        result = fixer.apply(self.CONTENT, self.FINDINGS, "app.py")
        self.assertIn("SEC-HARDCODED-SECRET", result["rules"])
        self.assertIn("REL-DEBUG-PRINT", result["rules"])
        self.assertIn("# keep this comment", result["content"])
        self.assertIn('password = os.environ["PASSWORD"]', result["content"])

    def test_create_fix_commits_rejects_over_max_files(self):
        class FakeClient:
            def get_pull_request(self, repository, number):
                return {
                    "head": {"ref": "main", "sha": "abc",
                             "repo": {"full_name": "org/repo"}},
                    "base": {"ref": "main"},
                }

            def get_file(self, repository, path, ref):
                return {"decoded_content": "print('debug')\n"}

        report = {
            "findings": [
                _finding("a.py", 1, "REL-DEBUG-PRINT"),
                _finding("b.py", 1, "REL-DEBUG-PRINT"),
            ]
        }
        fixer = SafeFixer(ast_fixer_enabled=True, max_fix_files=1)
        result = fixer.create_fix_commits(FakeClient(), "org/repo", 5, report)
        self.assertIsNone(result["branch"])
        self.assertIn("AST_FIX_MAX_FILES", result["note"])

    def test_default_fixer_is_disabled(self):
        self.assertFalse(SafeFixer().ast_fixer_enabled)
        self.assertIsNone(SafeFixer()._ast_fixer)


if __name__ == "__main__":
    unittest.main()
