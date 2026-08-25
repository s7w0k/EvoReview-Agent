import unittest

from evoagent.policy.models import (
    AgentPolicy,
    ExecutionBudget,
    ExecutionPolicy,
    RetryPolicy,
    ToolPermission,
    VerificationPolicy,
)


class ExecutionPolicyModelTest(unittest.TestCase):
    def test_default_policy_is_valid(self):
        policy = ExecutionPolicy(policy_id="p1")
        self.assertEqual(policy.risk_level, "low")
        self.assertEqual(policy.budget.max_steps, 1)
        self.assertEqual(policy.budget.max_tool_calls, 1)

    def test_round_trip_serialization(self):
        policy = ExecutionPolicy(
            policy_id="p1", policy_version=3, risk_level="high",
            budget=ExecutionBudget(max_steps=10, max_tool_calls=20),
            retry=RetryPolicy(max_retries=2, retryable_failures={"MODEL_TIMEOUT"}),
            verification=VerificationPolicy(verifier_required=True),
            agents=AgentPolicy(enabled_agents=["security"]),
            tool_permissions=[ToolPermission("run_tests", requires_sandbox=True)],
        )
        restored = ExecutionPolicy.from_dict(policy.to_dict())
        self.assertEqual(restored, policy)
        self.assertEqual(restored.retry.retryable_failures, {"MODEL_TIMEOUT"})

    def test_invalid_risk_level_rejected(self):
        with self.assertRaises(ValueError):
            ExecutionPolicy(policy_id="p", risk_level="extreme")

    def test_invalid_budget_rejected(self):
        with self.assertRaises(ValueError):
            ExecutionPolicy(
                policy_id="p",
                budget=ExecutionBudget(max_steps=0, max_tool_calls=5),
            )

    def test_tool_permission_lookup(self):
        policy = ExecutionPolicy(
            policy_id="p",
            tool_permissions=[ToolPermission("push_fix", allow=False)],
        )
        self.assertFalse(policy.allows("push_fix"))
        self.assertIsNone(policy.tool_permission("missing"))


if __name__ == "__main__":
    unittest.main()