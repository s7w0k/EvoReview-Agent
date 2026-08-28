"""Phase 4/11: real-HTTP A2A benchmark + typed factory regression tests."""
import unittest

from evoagent.a2a.evaluation import (
    HttpRemoteModeAdapter,
    LocalModeAdapter,
    run_http_benchmark,
)
from evoagent.a2a.factory import (
    build_agent_card,
    build_remote_reviewers_typed,
)
from evoagent.a2a.server import AgentServer
from evoagent.a2a.service import AgentServiceHost
from evoagent.reviewer import SecurityRuleReviewer

CASE = {
    "diff": "@@ -0 +1 @@\n+password = \"hunter2\"\n",
    "expected_findings": [{"path": "unknown", "line": 1, "severity": "high"}],
}


def _server(reviewer=None):
    card = build_agent_card("security-agent", "", deployment="http")
    host = AgentServiceHost(reviewer or SecurityRuleReviewer(), card)
    return AgentServer(host).start()


class TypedFactoryTest(unittest.TestCase):
    def test_name_pinned_to_agent_id(self):
        server = _server()
        self.addCleanup(server.stop)
        reviewers, registry = build_remote_reviewers_typed([server.endpoint])
        adapter = reviewers[0]
        # Coordinator routes + registry health lookups are keyed by agent id.
        self.assertEqual(adapter.name, "security-agent")
        self.assertEqual(adapter.agent_id, "security-agent")
        self.assertIsNotNone(registry.get("security-agent"))
        self.assertEqual(adapter.local_fallback.name, "local-rules")


class HttpBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.server = _server()
        self.addCleanup(self.server.stop)

    def test_run_http_benchmark_real_transport(self):
        report = run_http_benchmark([self.server.endpoint], timeout_seconds=5.0)
        self.assertIn("cases", report)
        self.assertIn("agents", report)
        self.assertGreater(len(report["cases"]), 0)
        self.assertIn("security-agent", report["agents"])
        agent = report["agents"]["security-agent"]
        a2a = agent["modes"]["remote_a2a"]["a2a"]
        # Plan section 14.2 runtime metrics are present and bounded.
        for key in ("remote_retry_rate", "fallback_rate", "trace_coverage",
                    "p99_latency_ms", "e2e_p95_latency_ms"):
            self.assertIn(key, a2a)
        self.assertEqual(a2a["trace_coverage"], 1.0)

    def test_http_adapter_metrics(self):
        remote = HttpRemoteModeAdapter(self.server.endpoint, timeout_seconds=5.0)
        local = LocalModeAdapter(SecurityRuleReviewer())
        self.assertTrue(local.run_case(CASE).success)
        result = remote.run_case(CASE)
        self.assertTrue(result.success)
        metrics = remote.a2a_metrics
        self.assertEqual(metrics["trace_coverage"], 1.0)
        self.assertGreaterEqual(metrics["p99_latency_ms"], 0.0)
        self.assertGreaterEqual(metrics["e2e_p95_latency_ms"], 0.0)
        self.assertEqual(metrics["remote_task_success"], 1.0)

    def test_frozen_dataset_clean_and_secret(self):
        from evoagent.a2a.evaluation import FROZEN_CASES
        has_secret = any(case["expected_findings"] for case in FROZEN_CASES)
        has_clean = any(not case["expected_findings"] for case in FROZEN_CASES)
        self.assertTrue(has_secret)
        self.assertTrue(has_clean)


if __name__ == "__main__":
    unittest.main()