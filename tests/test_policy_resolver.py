import unittest

from evoagent.policy.models import ExecutionBudget, ExecutionPolicy
from evoagent.policy.resolver import PolicyResolver
from evoagent.policy.risk import RiskProfile


class PolicyResolverTest(unittest.TestCase):
    def setUp(self):
        self.resolver = PolicyResolver()

    def test_low_risk_selects_single_agent(self):
        policy = self.resolver.resolve({}, RiskProfile(level="low"))
        self.assertLessEqual(len(policy.agents.enabled_agents), 1)

    def test_high_risk_forces_evidence(self):
        policy = self.resolver.resolve({}, RiskProfile(level="high"))
        self.assertTrue(policy.verification.evidence_required)
        self.assertEqual(policy.risk_level, "high")

    def test_tenant_override(self):
        tenant = {
            "budget": {"max_steps": 20},
            "agents": {"enabled_agents": ["security"]},
        }
        policy = self.resolver.resolve(
            {}, RiskProfile(level="low"), tenant_config=tenant
        )
        self.assertEqual(policy.budget.max_steps, 20)
        self.assertEqual(policy.agents.enabled_agents, ["security"])
        self.assertEqual(policy.metadata.get("override_source"), "tenant")

    def test_repository_override(self):
        repo = {"verification": {"verifier_required": True}}
        policy = self.resolver.resolve(
            {}, RiskProfile(level="low"), repository_config=repo
        )
        self.assertTrue(policy.verification.verifier_required)

    def test_task_override_highest_precedence(self):
        task = {"policy": {"budget": {"max_steps": 99}}}
        tenant = {"budget": {"max_steps": 5}}
        policy = self.resolver.resolve(
            task, RiskProfile(level="low"), tenant_config=tenant
        )
        self.assertEqual(policy.budget.max_steps, 99)
        self.assertTrue(policy.metadata.get("task_override"))

    def test_risk_escalates_not_lowers(self):
        # A low profile with a repo override that claims high risk stays low-capable.
        policy = self.resolver.resolve(
            {}, RiskProfile(level="low"),
            repository_config={"budget": {"max_steps": 100}},
        )
        self.assertEqual(policy.risk_level, "low")

    def test_validate_rejects_invalid_policy(self):
        with self.assertRaises(ValueError):
            self.resolver.validate(
                ExecutionPolicy(
                    policy_id="bad",
                    budget=ExecutionBudget(max_steps=0, max_tool_calls=5),
                )
            )


if __name__ == "__main__":
    unittest.main()