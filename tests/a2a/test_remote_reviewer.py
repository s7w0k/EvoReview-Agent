"""Phase 4/6 acceptance: RemoteReviewerAdapter outputs match the local
SecurityRuleReviewer and emits CollaborationBus trace events."""
import unittest

from evoagent.a2a.adapters import RemoteReviewerAdapter
from evoagent.a2a.http_transport import HttpJsonRpcA2ATransport
from evoagent.a2a.factory import build_agent_card
from evoagent.a2a.models import AgentCard
from evoagent.a2a.server import AgentServer
from evoagent.a2a.service import AgentServiceHost
from evoagent.agents import CollaborationBus
from evoagent.diff_parser import parse_unified_diff
from evoagent.reviewer import SecurityRuleReviewer

DIFF = "@@ -0 +1 @@\n+eval(request.body)\n"


class RemoteReviewerTest(unittest.TestCase):
    def setUp(self):
        card = build_agent_card("security-agent", "http://local/a2a", deployment="http")
        host = AgentServiceHost(SecurityRuleReviewer(), card)
        self.server = AgentServer(host).start()
        self.addCleanup(self.server.stop)
        wire_card = AgentCard.from_dict(self.server.card().to_dict())
        wire_card.endpoint = self.server.endpoint
        self.card = wire_card

    def _adapter(self, bus=None):
        transport = HttpJsonRpcA2ATransport()
        return RemoteReviewerAdapter(
            self.card.to_dict(), transport, bus=bus, timeout_seconds=10,
        )

    def test_local_remote_equivalence(self):
        parsed = parse_unified_diff(DIFF)
        local = SecurityRuleReviewer().review(DIFF, parsed)
        remote = self._adapter().review(DIFF, parsed)
        self.assertEqual(
            sorted((f.path, f.line, f.rule_id) for f in local),
            sorted((f.path, f.line, f.rule_id) for f in remote),
        )

    def test_review_assignment_interface(self):
        parsed = parse_unified_diff(DIFF)
        adapter = self._adapter()
        findings = adapter.review_assignment(
            DIFF, parsed,
            {"agent": adapter.name, "objective": "x", "files": [], "risk_domains": ["security"],
             "assignment_id": "A01", "round": 1},
            feedback=["check injection"], inbox=[],
        )
        self.assertGreaterEqual(len(findings), 1)

    def test_bus_trace_events(self):
        bus = CollaborationBus(task_id="task-1")
        adapter = self._adapter(bus=bus)
        adapter.review(DIFF, parse_unified_diff(DIFF))
        kinds = [m.kind for m in bus.messages]
        self.assertIn("remote_task_submitted", kinds)
        self.assertIn("remote_artifact_received", kinds)


if __name__ == "__main__":
    unittest.main()