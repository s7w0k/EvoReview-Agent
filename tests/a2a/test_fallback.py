"""Phase 7 acceptance: Remote failure falls over to a backup Remote Agent and
finally to the local reviewer."""
import unittest

from evoagent.a2a.adapters import RemoteReviewerAdapter
from evoagent.a2a.errors import A2AProtocolError
from evoagent.a2a.factory import build_agent_card
from evoagent.a2a.http_transport import HttpJsonRpcA2ATransport
from evoagent.a2a.models import AgentCard
from evoagent.a2a.resilience import RetryPolicy
from evoagent.a2a.server import AgentServer
from evoagent.a2a.service import AgentServiceHost
from evoagent.diff_parser import parse_unified_diff
from evoagent.reviewer import SecurityRuleReviewer

DIFF = "@@ -0 +1 @@\n+eval(user_data)\n"


def _server(fail_on=None):
    card = build_agent_card("security-agent", "http://local/a2a", deployment="http")
    host = AgentServiceHost(SecurityRuleReviewer(), card, fail_on=fail_on)
    return AgentServer(host).start()


def _wire(server):
    card = AgentCard.from_dict(server.card().to_dict())
    card.endpoint = server.endpoint
    return card.to_dict()


class FallbackTest(unittest.TestCase):
    def _down_card(self):
        return build_agent_card("security-agent", "http://127.0.0.1:1/a2a", deployment="http")

    def test_local_fallback_on_primary_down(self):
        adapter = RemoteReviewerAdapter(
            self._down_card(), HttpJsonRpcA2ATransport(timeout_seconds=0.3),
            local_fallback=SecurityRuleReviewer(),
            retry=RetryPolicy(max_attempts=1),
        )
        findings = adapter.review(DIFF, parse_unified_diff(DIFF))
        self.assertGreaterEqual(len(findings), 1)

    def test_backup_remote_used_when_primary_500(self):
        primary = _server(fail_on={"http": {"mode": "status-code", "status": 500}})
        backup = _server()
        self.addCleanup(primary.stop)
        self.addCleanup(backup.stop)

        backup_card = _wire(backup)
        adapter = RemoteReviewerAdapter(
            _wire(primary), HttpJsonRpcA2ATransport(),
            backup_card=backup_card, backup_transport=HttpJsonRpcA2ATransport(),
            retry=RetryPolicy(max_attempts=1),
        )
        findings = adapter.review(DIFF, parse_unified_diff(DIFF))
        self.assertGreaterEqual(len(findings), 1)

    def test_identity_error_fails_fast_without_local_fallback(self):
        # A protocol/schema violation must NOT be silently swallowed by fallback.
        from evoagent.a2a.errors import A2AProtocolError
        fake_card = self._down_card()
        adapter = RemoteReviewerAdapter(
            fake_card, _AlwaysProtocolError(),
            local_fallback=SecurityRuleReviewer(),
            retry=RetryPolicy(max_attempts=3),
        )
        with self.assertRaises(A2AProtocolError):
            adapter.review(DIFF, parse_unified_diff(DIFF))


class _AlwaysProtocolError(HttpJsonRpcA2ATransport):
    def submit_task(self, card, task):
        raise A2AProtocolError("unsupported version", target_agent=card.agent_id)


if __name__ == "__main__":
    unittest.main()