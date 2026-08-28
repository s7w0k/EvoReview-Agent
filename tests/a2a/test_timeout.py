"""Phase 7 acceptance: timeouts are classified + surfaced through the resilient
transport, and drive the remote timeout metric."""
import unittest

from evoagent.a2a.factory import build_agent_card
from evoagent.a2a.http_transport import HttpJsonRpcA2ATransport
from evoagent.a2a.models import A2ATask, AgentCard
from evoagent.a2a.resilience import RetryPolicy
from evoagent.a2a.server import AgentServer
from evoagent.a2a.service import AgentServiceHost
from evoagent.a2a.transport import ResilientTransport
from evoagent.reviewer import SecurityRuleReviewer

DIFF = "@@ -0 +1 @@\n+print('hi')\n"


def _task():
    return A2ATask(task_id="t-to", assignment_id="A", sender="s",
                   recipient="security-agent", task_type="review-assignment",
                   input={"diff": DIFF}, correlation_id="A")


class TimeoutTest(unittest.TestCase):
    def _slow_server(self, delay):
        card = build_agent_card("security-agent", "http://local/a2a", deployment="http")
        host = AgentServiceHost(SecurityRuleReviewer(), card, delay_seconds=delay)
        return AgentServer(host).start()

    def test_remote_timeout_raised(self):
        server = self._slow_server(2.0)
        self.addCleanup(server.stop)
        card = AgentCard.from_dict(server.card().to_dict())
        card.endpoint = server.endpoint

        from evoagent.a2a.errors import A2ATimeoutError
        base = HttpJsonRpcA2ATransport(timeout_seconds=0.2)
        resilient = ResilientTransport(base, retry=RetryPolicy(max_attempts=1))
        with self.assertRaises(A2ATimeoutError):
            resilient.submit_task(card, _task())

    def test_fast_server_is_not_timeout(self):
        server = AgentServer(AgentServiceHost(
            SecurityRuleReviewer(),
            build_agent_card("security-agent", "http://l/a2a", deployment="http"),
        )).start()
        self.addCleanup(server.stop)
        card = AgentCard.from_dict(server.card().to_dict())
        card.endpoint = server.endpoint
        resilient = ResilientTransport(
            HttpJsonRpcA2ATransport(timeout_seconds=5.0), retry=RetryPolicy(max_attempts=1))
        record = resilient.submit_task(card, _task())
        self.assertEqual(record["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()