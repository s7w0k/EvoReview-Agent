"""Phase 2 acceptance: the same A2ATask through InProcess and HTTP transports
yields the same A2AArtifact structure."""
import unittest

from evoagent.a2a.factory import build_agent_card
from evoagent.a2a.http_transport import HttpJsonRpcA2ATransport
from evoagent.a2a.inprocess_transport import InProcessA2ATransport
from evoagent.a2a.models import A2ATask
from evoagent.a2a.server import AgentServer
from evoagent.a2a.service import AgentServiceHost
from evoagent.reviewer import SecurityRuleReviewer

DIFF = "@@ -0 +1 @@\n+password = \"hunter2\"\n"


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.card = build_agent_card(
            "security-agent", "http://127.0.0.1:1/a2a", deployment="http")
        self.task = A2ATask(
            task_id="review-001", assignment_id="A01", sender="planner",
            recipient="security-agent", task_type="review-assignment",
            input={"diff": DIFF}, correlation_id="A01",
        )

    def _host(self):
        return AgentServiceHost(SecurityRuleReviewer(), self.card)

    def test_inprocess_to_dict_matches_http_command(self):
        inproc = InProcessA2ATransport(self._host())
        inproc_result = inproc.submit_task(self.card, self.task)
        inproc_artifacts = inproc.get_artifacts(self.card, self.task.task_id)

        server = AgentServer(self._host()).start()
        try:
            card = {"agent_id": "security-agent", "name": "Security Review Agent",
                    "endpoint": server.endpoint, "protocol_version": "v1",
                    "capabilities": ["code-review"], "domains": ["security"],
                    "supported_task_types": ["review-assignment"], "version": "1.0.0",
                    "deployment": "http"}
            from evoagent.a2a.models import AgentCard
            http = HttpJsonRpcA2ATransport()
            http_result = http.submit_task(AgentCard.from_dict(card), self.task)
            http_artifacts = http.get_artifacts(AgentCard.from_dict(card), self.task.task_id)
        finally:
            server.stop()

        self.assertEqual(inproc_artifacts[0]["artifact_type"],
                         http_artifacts[0]["artifact_type"])
        self.assertEqual(
            inproc_artifacts[0]["content"]["findings"],
            http_artifacts[0]["content"]["findings"],
        )
        self.assertEqual(inproc_result["status"], "COMPLETED")
        self.assertEqual(http_result["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()