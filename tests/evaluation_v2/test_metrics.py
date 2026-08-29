"""tests/evaluation_v2/test_metrics.py

Assert the fixed scorer (reused verbatim from V1 via ``EndToEndEvaluationHarness``)
yields correct tp/fp/fn and detection formulas, and that governance telemetry is
carried through (plan phase 4, Rule 3 : the scorer is never modified).
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

from evoagent.evaluation_v2 import metrics  # noqa: E402
from evoagent.evaluation_v2.adapters import EvaluationExecutionResult  # noqa: E402
from evoagent.models import Finding, Severity  # noqa: E402

from helpers import make_clean_case, make_risk_case  # noqa: E402


def _finding_for(case, rule_id="SEC-EVAL", severity=Severity.CRITICAL):
    path = case["expected_findings"][0]["path"]
    line = case["expected_findings"][0]["start_line"]
    return Finding(rule_id=rule_id, severity=severity, title="t", explanation="e",
                   path=path, line=line, evidence="", fix="", test="",
                   confidence=0.9)


class MetricsTests(unittest.TestCase):
    def test_perfect_risk_case_scores_tp(self):
        case = make_risk_case(added_line="    result = eval(value)\n")
        execution = EvaluationExecutionResult(findings=[_finding_for(case)])
        scored = metrics.score_case(case, execution)
        self.assertEqual(1, scored["tp"])
        self.assertEqual(0, scored["fp"])
        self.assertEqual(0, scored["fn"])

    def test_missed_risk_case_scores_fn(self):
        case = make_risk_case(added_line="    result = eval(value)\n")
        execution = EvaluationExecutionResult(findings=[])
        scored = metrics.score_case(case, execution)
        self.assertEqual(0, scored["tp"])
        self.assertEqual(1, scored["fn"])

    def test_spurious_finding_scores_fp(self):
        clean = make_clean_case()
        execution = EvaluationExecutionResult(findings=[
            Finding(rule_id="SEC-EVAL", severity=Severity.CRITICAL, title="t",
                    explanation="e", path="src/clean.py", line=0, evidence="",
                    fix="", test="", confidence=0.9)])
        scored = metrics.score_case(clean, execution)
        self.assertEqual(1, scored["fp"])
        self.assertTrue(scored["clean_hit"] is False)

    def test_clean_case_hits_clean(self):
        clean = make_clean_case()
        scored = metrics.score_case(clean, EvaluationExecutionResult(findings=[]))
        self.assertTrue(scored["clean_hit"])

    def test_detection_metrics_reuse_v1_formulas(self):
        case = make_risk_case(added_line="    result = eval(value)\n")
        execution = EvaluationExecutionResult(findings=[_finding_for(case)])
        scored = [metrics.score_case(case, execution)]
        det = metrics.detection_metrics(scored)
        self.assertEqual(1.0, det["recall"])
        self.assertEqual(1.0, det["precision"])
        self.assertEqual(1.0, det["f1"])
        # High / critical recall requires the severity to be flagged as high-risk.
        self.assertEqual(1.0, det["high_risk_recall"])

    def test_critical_misses_only_count_critical_gold(self):
        critical = make_risk_case(case_id='pr-0101', severity='critical')
        high = make_risk_case(case_id='pr-0102', severity='high')
        missed = EvaluationExecutionResult(findings=[])
        scored = [metrics.score_case(critical, missed), metrics.score_case(high, missed)]
        det = metrics.detection_metrics(scored)
        self.assertEqual(2, det['high_risk_total'])
        self.assertEqual(2, det['high_risk_misses'])
        self.assertEqual(1, det['critical_total'])
        self.assertEqual(1, det['critical_misses'])

    def test_runtime_metrics_aggregate_governance_telemetry(self):
        case = make_risk_case(added_line="    result = eval(value)\n")
        execution = EvaluationExecutionResult(
            findings=[_finding_for(case)], agent_steps=4, tool_calls=2,
            decision_trace_created=True, replay_snapshot_created=True,
            policy_version=3, policy_id="baseline-high", latency_ms=100.0)
        scored = metrics.score_case(case, execution)
        rt = metrics.runtime_metrics([scored])
        self.assertEqual(1.0, rt["execution_success_rate"])
        self.assertEqual(1.0, rt["decision_trace_coverage"])
        self.assertEqual(1.0, rt["replay_snapshot_coverage"])
        self.assertEqual(4.0, rt["avg_agent_steps"])
        self.assertEqual(3, rt["avg_policy_version"])


if __name__ == "__main__":
    unittest.main()
