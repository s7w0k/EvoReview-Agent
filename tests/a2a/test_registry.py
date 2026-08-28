"""Phase 3 acceptance: registry register/discover/match by capability/domain/
health/version."""
import unittest

from evoagent.a2a.registry import AgentRegistry


def _card(agent_id, domains, capabilities, version="1.0.0", health="healthy"):
    return {
        "agent_id": agent_id, "name": agent_id, "endpoint": "http://x/a2a",
        "protocol_version": "v1", "domains": domains,
        "capabilities": capabilities,
        "supported_task_types": ["review-assignment"],
        "version": version, "health_status": health, "deployment": "http",
    }


class AgentRegistryTest(unittest.TestCase):
    def test_register_and_get(self):
        reg = AgentRegistry()
        reg.register(_card("security-agent", ["security"], ["code-review"]))
        self.assertEqual(reg.get("security-agent")["agent_id"], "security-agent")

    def test_match_by_domain(self):
        reg = AgentRegistry()
        reg.register_many([
            _card("security-agent", ["security"], ["security-review"]),
            _card("reliability-agent", ["reliability", "correctness"], ["reliability-review"]),
        ])
        matched = reg.match(required_domains=["correctness"])
        self.assertEqual([m["agent_id"] for m in matched], ["reliability-agent"])

    def test_match_by_capability(self):
        reg = AgentRegistry()
        reg.register_many([
            _card("security-agent", ["security"], ["security-review"]),
            _card("reliability-agent", ["reliability"], ["reliability-review"]),
        ])
        matched = reg.match(required_capabilities=["reliability-review"])
        self.assertEqual([m["agent_id"] for m in matched], ["reliability-agent"])

    def test_unhealthy_excluded(self):
        reg = AgentRegistry()
        reg.register(_card("security-agent", ["security"], ["review"], health="unhealthy"))
        self.assertEqual(reg.match(required_domains=["security"]), [])

    def test_version_gate(self):
        reg = AgentRegistry()
        reg.register(_card("a", ["security"], ["review"], version="1.0.0"))
        reg.register(_card("b", ["security"], ["review"], version="1.5.0"))
        matched = reg.match(required_domains=["security"], min_version="1.2.0")
        self.assertEqual([m["agent_id"] for m in matched], ["b"])

    def test_mark_unhealthy_then_recovered(self):
        reg = AgentRegistry()
        reg.register(_card("a", ["security"], ["review"]))
        reg.mark_unhealthy("a")
        self.assertEqual(reg.match(required_domains=["security"]), [])
        reg.mark_healthy("a")
        self.assertEqual(len(reg.match(required_domains=["security"])), 1)


if __name__ == "__main__":
    unittest.main()