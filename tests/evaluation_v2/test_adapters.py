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
    build_evaluation_leader,
)
from evoagent.models import Finding, Severity  # noqa: E402
from evoagent.skill_evolution import DeclarativeSkillReviewer  # noqa: E402

from helpers import make_clean_case, make_risk_case  # noqa: E402


class _CapturingService:
    """Stub that captures the tool-context composed by build_evaluation_leader."""

    def __init__(self):
        self.captured = None

    def _build_leader(self, reviewers, execution_policy=None, tool_context_config=None):
        self.captured = tool_context_config or {}
        return object()


class LeaderDomainCompositionTests(unittest.TestCase):
    def _skill(self, domain):
        return DeclarativeSkillReviewer({
            'name': 'evolved-%s' % domain, 'description': 'x',
            'rules': [{
                'rule_id': 'CWE-703', 'severity': 'medium',
                'match': 'except Exception:', 'domain': domain,
            }],
        }, version=1)

    def test_security_candidate_joins_security_reviewers(self):
        svc = _CapturingService()
        candidate = self._skill('security')
        build_evaluation_leader(svc, None, evolved_skill=candidate,
                                candidate_id='cand-sec', reviewers=[])
        cfg = svc.captured
        self.assertIn('cand-sec', cfg['security_reviewer_ids'])
        self.assertNotIn('cand-sec', cfg['reliability_reviewer_ids'])

    def test_reliability_candidate_joins_reliability_reviewers(self):
        svc = _CapturingService()
        candidate = self._skill('reliability')
        build_evaluation_leader(svc, None, evolved_skill=candidate,
                                candidate_id='cand-rel', reviewers=[])
        cfg = svc.captured
        self.assertNotIn('cand-rel', cfg['security_reviewer_ids'])
        self.assertIn('cand-rel', cfg['reliability_reviewer_ids'])
        self.assertEqual(['reliability-rule@1', 'cand-rel'],
                         cfg['reliability_reviewer_ids'])

    def test_shared_candidate_joins_both_reviewers(self):
        svc = _CapturingService()
        candidate = self._skill('shared')
        build_evaluation_leader(svc, None, evolved_skill=candidate,
                                candidate_id='cand-sh', reviewers=[])
        cfg = svc.captured
        self.assertIn('cand-sh', cfg['security_reviewer_ids'])
        self.assertIn('cand-sh', cfg['reliability_reviewer_ids'])


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