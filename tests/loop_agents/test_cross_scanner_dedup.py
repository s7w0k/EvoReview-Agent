'''Cross-scanner canonical de-duplication (plan Phase 5).

The SEC-EVAL semantics reported by the AST analyzer are the same issue as the
regex's SEC-EVAL finding; they must collapse into one canonical finding at the
same location, while distinct vulnerabilities on the same line stay separate.
'''
import unittest

from evoagent.finding_identity import (
    canonical_family,
    canonical_identity,
    merge_cross_scanner,
)
from evoagent.loop_agents.tools import deduplicate_findings
from evoagent.models import Finding, Severity


def _finding(rule_id, line=1, path="app.py", severity="critical",
             confidence=0.8, evidence="eval(user_input)"):
    return Finding(
        rule_id=rule_id, severity=Severity(severity), title=rule_id,
        explanation="e", path=path, line=line, evidence=evidence,
        fix="f", test="t", confidence=confidence,
    )


class CanonicalIdentityTests(unittest.TestCase):
    def test_rule_and_semantic_share_family(self):
        self.assertEqual(
            canonical_family("SEC-EVAL"), canonical_family("SEM-TAINTED-EXEC"))

    def test_unrelated_rules_stay_distinct(self):
        self.assertNotEqual(
            canonical_family("SEC-EVAL"), canonical_family("SEC-SQL-CONCAT"))

    def test_identity_is_family_path_line(self):
        a = _finding("SEC-EVAL", line=4, path="a.py")
        b = _finding("SEM-TAINTED-EXEC", line=4, path="a.py")
        self.assertEqual(canonical_identity(a), canonical_identity(b))
        c = _finding("SEM-TAINTED-EXEC", line=5, path="a.py")
        self.assertNotEqual(canonical_identity(a), canonical_identity(c))


class CrossScannerMergeTests(unittest.TestCase):
    def test_same_issue_one_finding(self):
        merged = deduplicate_findings([
            _finding("SEC-EVAL", severity="critical", confidence=0.9),
            _finding("SEM-TAINTED-EXEC", severity="high", confidence=0.8),
        ])
        self.assertEqual(1, len(merged))
        self.assertEqual("SEC-EVAL", merged[0].rule_id)  # strongest primary kept

    def test_different_vulnerabilities_same_line_kept(self):
        findings = deduplicate_findings([
            _finding("SEC-EVAL", severity="critical"),
            _finding("SEC-SQL-CONCAT", severity="high"),
        ])
        self.assertEqual(2, len(findings))

    def test_same_rule_duplicates_collapse(self):
        findings = merge_cross_scanner([
            _finding("SEC-EVAL", confidence=0.7),
            _finding("SEC-EVAL", confidence=0.9),
        ])
        self.assertEqual(1, len(findings))
        self.assertEqual(0.9, findings[0].confidence)

    def test_different_line_not_merged(self):
        findings = merge_cross_scanner([
            _finding("SEC-EVAL", line=2),
            _finding("SEM-TAINTED-EXEC", line=3),
        ])
        self.assertEqual(2, len(findings))


if __name__ == "__main__":
    unittest.main()