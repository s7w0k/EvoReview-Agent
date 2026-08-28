"""tests/evaluation_v2/test_adapters.py

Verify that the four unified adapters produce an ``EvaluationExecutionResult`` in
the same shape so the fixed scorer can consume them (plan phases 3-6).
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

from evoagent.evaluation_v2.adapters import (  # noqa: E402
    EvaluationExecutionResult,
    LegacyMultiAgentEvaluationAdapter,
    SingleAgentEvaluationAdapter,
)
from evoagent.models import Finding, Severity  # noqa: E402

from helpers import make_clean_case, make_risk_case  # noqa: E402


class AdapterContractTests(unittest.TestCase):
    def test_single_agent_detects_eval_on_added_line(self):
        adapter = SingleAgentEvaluationAdapter()
        case = make_risk_case(added_line="    result = eval(value)\n")
        result = adapter.review_case(case)
        self.assertIsInstance(result, EvaluationExecutionResult)
        self.assertTrue(result.success)
        self.assertTrue(result.findings, "single agent should flag the eval")
        self.assertEqual(result.resolved_policy, {"target": "legacy-reviewer"})

    def test_legacy_multi_agent_detects_cwe_sql_concat(self):
        adapter = LegacyMultiAgentEvaluationAdapter()
        case = make_risk_case(
            rule_id="SEC-SQL-CONCAT", severity="high",
            added_line="    query(\"SELECT * FROM t WHERE id=\" + uid)\n")
        result = adapter.review_case(case)
        self.assertTrue(result.findings)
        codes = {f.rule_id for f in result.findings}
        self.assertIn("SEC-SQL-CONCAT", codes)

    def test_clean_case_yields_no_findings(self):
        adapter = SingleAgentEvaluationAdapter()
        result = adapter.review_case(make_clean_case())
        self.assertEqual([], result.findings)

    def test_findings_are_real_finding_objects(self):
        adapter = SingleAgentEvaluationAdapter()
        result = adapter.review_case(
            make_risk_case(added_line="    result = eval(value)\n"))
        self.assertTrue(all(isinstance(f, Finding) for f in result.findings))

    def test_result_to_dict_is_flattenable(self):
        result = EvaluationExecutionResult(
            findings=[Finding(rule_id="SEC-EVAL", severity=Severity.CRITICAL,
                              title="t", explanation="e", path="p", line=1,
                              evidence="", fix="", test="", confidence=0.9)],
            latency_ms=1.5, agent_steps=2,
        )
        as_dict = result.to_dict()
        self.assertEqual(2, as_dict["agent_steps"])
        self.assertEqual("critical", as_dict["findings"][0]["severity"])


if __name__ == "__main__":
    unittest.main()