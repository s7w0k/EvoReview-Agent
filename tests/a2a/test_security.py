"""Phase 8 acceptance: token auth, authorization policy and artifact
sanitisation/validation."""
import unittest

from evoagent.a2a.governance import (
    ArtifactSanitizer, AuthorizationPolicy, verify_token,
)
from evoagent.a2a.factory import build_agent_card
from evoagent.a2a.http_transport import HttpJsonRpcA2ATransport
from evoagent.a2a.models import AgentCard
from evoagent.a2a.server import AgentServer
from evoagent.a2a.service import AgentServiceHost
from evoagent.reviewer import SecurityRuleReviewer

DIFF = "@@ -0 +1 @@\n+print('x')\n"


class AuthTest(unittest.TestCase):
    def test_verify_token_constant_time(self):
        self.assertTrue(verify_token("secret", "secret"))
        self.assertFalse(verify_token("secret", "wrong"))
        self.assertTrue(verify_token("", "anything"))  # disabled auth

    def test_unauthorized_when_token_mismatch(self):
        card = build_agent_card("security-agent", "http://local/a2a", deployment="http")
        host = AgentServiceHost(SecurityRuleReviewer(), card, token="server-secret")
        server = AgentServer(host).start()
        self.addCleanup(server.stop)

        http_card = AgentCard.from_dict(server.card().to_dict())
        http_card.endpoint = server.endpoint

        from evoagent.a2a.errors import A2AUnauthorizedError
        with self.assertRaises(A2AUnauthorizedError):
            HttpJsonRpcA2ATransport(token="wrong-token").submit_task(
                http_card,
                _task(),
            )
        # correct token succeeds
        record = HttpJsonRpcA2ATransport(token="server-secret").submit_task(http_card, _task())
        self.assertEqual(record["status"], "COMPLETED")


class AuthorizationPolicyTest(unittest.TestCase):
    def test_allows_scoped(self):
        policy = AuthorizationPolicy(
            "security-agent",
            allowed_capabilities=["security-review"],
            tenant_scope=["default"],
        )
        self.assertTrue(policy.allows("security-review", "default"))
        self.assertFalse(policy.allows("reliability-review", "default"))
        self.assertFalse(policy.allows("security-review", "other-tenant"))


class SanitizerTest(unittest.TestCase):
    def test_validate_ok(self):
        from evoagent.a2a.models import A2AArtifact
        artifact = A2AArtifact(
            artifact_id="a", task_id="t", artifact_type="review-findings",
            producer="security-agent",
            content={"findings": [{"path": "a.py", "line": 1, "rule_id": "R",
                                   "title": "t"}]},
        )
        validated = ArtifactSanitizer().validate(artifact.to_dict())
        self.assertEqual(validated.artifact_id, "a")

    def test_duplicate_finding_rejected(self):
        from evoagent.a2a.models import A2AArtifact
        artifact = A2AArtifact(
            artifact_id="a", task_id="t", artifact_type="review-findings",
            producer="security-agent",
            content={"findings": [
                {"path": "a.py", "line": 1, "rule_id": "R", "title": "t"},
                {"path": "a.py", "line": 1, "rule_id": "R", "title": "t"},
            ]},
        )
        with self.assertRaises(ValueError):
            ArtifactSanitizer().validate(artifact.to_dict())

    def test_sanitize(self):
        from evoagent.a2a.models import A2AArtifact
        artifact = A2AArtifact(
            artifact_id="a", task_id="t", artifact_type="review-findings",
            producer="security-agent",
            content={"findings": [{"path": "a.py", "line": 1, "rule_id": "R",
                                   "title": "<script>alert(1)</script>",
                                   "explanation": "note: will run calc.exe"}]},
        )
        safe = ArtifactSanitizer().sanitize(artifact)
        title = safe.content["findings"][0]["title"]
        self.assertEqual(title, "[sanitized]alert(1)[sanitized]")


def _task():
    from evoagent.a2a.models import A2ATask
    return A2ATask(task_id="t-sec", assignment_id="A", sender="s",
                   recipient="security-agent", task_type="review-assignment",
                   input={"diff": DIFF}, correlation_id="A")


if __name__ == "__main__":
    unittest.main()