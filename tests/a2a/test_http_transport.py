"""Phase 4 acceptance: HTTP transport end-to-end against a live agent server
(discover / submit / get / cancel / artifact.list)."""
import unittest

from evoagent.a2a.http_transport import HttpJsonRpcA2ATransport
from evoagent.a2a.models import A2ATask, AgentCard
from evoagent.a2a.server import AgentServer
from evoagent.a2a.service import AgentServiceHost
from evoagent.a2a.factory import build_agent_card
from evoagent.reviewer import SecurityRuleReviewer

DIFF = "@@ -0 +1 @@\n+eval(user_input)\n"


def _live_server(fail_on=None, delay=0.0):
    card = build_agent_card("security-agent", "http://local/a2a", deployment="http")
    host = AgentServiceHost(SecurityRuleReviewer(), card,
                            fail_on=fail_on, delay_seconds=delay)
    return AgentServer(host).start()


class HttpTransportTest(unittest.TestCase):
    def setUp(self):
        self.server = _live_server()
        self.addCleanup(self.server.stop)
        self.card = AgentCard.from_dict(self.server.card().to_dict())
        self.card.endpoint = self.server.endpoint
        self.transport = HttpJsonRpcA2ATransport()

    def _task(self):
        return A2ATask(task_id="t-http-1", assignment_id="A01", sender="planner",
                       recipient="security-agent", task_type="review-assignment",
                       input={"diff": DIFF}, correlation_id="A01")

    def test_discover(self):
        card = self.transport.discover(self.server.endpoint)
        self.assertEqual(card["agent_id"], "security-agent")

    def test_submit_and_lifecycle(self):
        task = self._task()
        record = self.transport.submit_task(self.card, task)
        self.assertEqual(record["status"], "COMPLETED")
        state = self.transport.get_task(self.card, task.task_id)
        self.assertEqual(state["task_id"], task.task_id)
        artifacts = self.transport.get_artifacts(self.card, task.task_id)
        self.assertGreaterEqual(len(artifacts), 1)
        self.assertEqual(len(artifacts[0]["content"]["findings"]), 1)

    def test_cancel(self):
        task = self._task()
        self.transport.submit_task(self.card, task)
        state = self.transport.cancel_task(self.card, task.task_id)
        self.assertIn(state["status"], {"CANCELLED", "COMPLETED"})

    def test_endpoint_unreachable(self):
        from evoagent.a2a.errors import A2AConnectionError
        transport = HttpJsonRpcA2ATransport(timeout_seconds=0.5)
        bad_card = AgentCard.from_dict(card_dict("http://127.0.0.1:9/a2a"))
        with self.assertRaises(A2AConnectionError):
            transport.submit_task(bad_card, self._task())


class HttpFailureInjectionTest(unittest.TestCase):
    def test_500_and_error_mapping(self):
        server = _live_server(fail_on={"http": {"mode": "status-code", "status": 500}})
        self.addCleanup(server.stop)
        card = AgentCard.from_dict(server.card().to_dict())
        card.endpoint = server.endpoint
        from evoagent.a2a.errors import A2AUnavailableError
        with self.assertRaises(A2AUnavailableError):
            HttpJsonRpcA2ATransport().submit_task(card, task_of())


def card_dict(endpoint):
    return build_agent_card("security-agent", endpoint, deployment="http")


def task_of():
    return A2ATask(task_id="t-inj", assignment_id="A", sender="s",
                   recipient="security-agent", task_type="review-assignment",
                   input={"diff": DIFF}, correlation_id="A")


if __name__ == "__main__":
    unittest.main()