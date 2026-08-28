"""Phase 11 acceptance: Evaluation Harness V3 Local vs Remote comparison and
functional equivalence on a small frozen dataset."""
import unittest

from evoagent.a2a.evaluation import (
    LocalModeAdapter, RemoteModeAdapter, compare_detection_local_remote,
    compare_local_remote,
)
from evoagent.a2a.factory import build_agent_card, build_inprocess_reviewers
from evoagent.a2a.server import AgentServer
from evoagent.a2a.service import AgentServiceHost
from evoagent.a2a.telemetry import A2AMetrics
from evoagent.diff_parser import parse_unified_diff
from evoagent.reviewer import SecurityRuleReviewer

CASE1 = {
    "id": "case-0001", "repository": "repo", "pull_request": 1, "split": "validation",
    "diff": "@@ -0 +1 @@\n+password = \"hunter2\"\n",
    "expected_findings": [{"path": "x.py", "line": 1, "severity": "high"}],
}
CASE2 = {
    "id": "case-0002", "repository": "repo", "pull_request": 2, "split": "validation",
    "diff": "@@ -0 +1 @@\n+# comment only\n",
    "expected_findings": [],
}


class V3EvaluationTest(unittest.TestCase):
    def setUp(self):
        card = build_agent_card("security-agent", "http://local/a2a", deployment="http")
        host = AgentServiceHost(SecurityRuleReviewer(), card)
        self.server = AgentServer(host).start()
        self.addCleanup(self.server.stop)

    def _remote_adapter(self):
        reviewers, _registry = build_inprocess_reviewers([self.server.host])
        return reviewers[0]

    def test_local_vs_remote_comparison(self):
        local_adapter = LocalModeAdapter(SecurityRuleReviewer())
        remote_adapter = RemoteModeAdapter(self._remote_adapter())
        report = compare_local_remote([CASE1, CASE2], local_adapter, remote_adapter)
        self.assertIn("modes", report)
        self.assertIn("report_table", report)

    def test_detection_equivalence(self):
        local = SecurityRuleReviewer()
        remote = self._remote_adapter()
        parsed = parse_unified_diff(CASE1["diff"])
        findings_local = local.review(CASE1["diff"], parsed)
        findings_remote = remote.review(CASE1["diff"], parsed)
        score = compare_detection_local_remote(
            CASE1["expected_findings"], findings_local, findings_remote)
        self.assertTrue(score["equivalent"])
        self.assertEqual(score["local"]["tp"], score["remote_a2a"]["tp"])

    def test_a2a_metrics_recorded(self):
        metrics = A2AMetrics(mirror=False)
        metrics.record_request("task.submit", "security-agent", "success")
        metrics.record_failure("security-agent", "A2ATimeoutError")
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["a2a_requests_total"], 1)
        self.assertEqual(snapshot["a2a_request_failures_total"], 1)


if __name__ == "__main__":
    unittest.main()