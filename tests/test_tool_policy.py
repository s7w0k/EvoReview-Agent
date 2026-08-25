import unittest

from evoagent.policy.models import (
    ExecutionBudget,
    ExecutionPolicy,
    RetryPolicy,
    ToolPermission,
)
from evoagent.policy.tool_policy import (
    ToolDecision,
    ToolMetadata,
    ToolPermissionDenied,
    ToolPolicyEngine,
)
from evoagent.runtime import AgentTool
from evoagent.tools.governed_registry import GovernedToolRegistry


def handler(**arguments):
    return dict(arguments)


META = {
    "read_file": ToolMetadata("read_file", risk_level="low"),
    "run_tests": ToolMetadata(
        "run_tests", risk_level="medium", requires_sandbox=True,
    ),
    "push_fix": ToolMetadata(
        "push_fix", risk_level="high", side_effect=True, idempotent=False,
        requires_approval=True, allowed_agents=["deployer"],
    ),
}

TOOLS = [
    AgentTool("read_file", "read", {"properties": {"path": {"type": "string"}}}, handler),
    AgentTool("run_tests", "test", {}, handler),
    AgentTool("push_fix", "push", {}, handler),
]


def low_policy():
    return ExecutionPolicy(
        policy_id="low", risk_level="low",
        budget=ExecutionBudget(max_steps=5, max_tool_calls=10, max_wall_time_seconds=60),
        retry=RetryPolicy(max_retries=0),
        tool_permissions=[
            ToolPermission("read_file", allow=True),
            ToolPermission("run_tests", allow=False),
            ToolPermission("push_fix", allow=False, requires_approval=True),
        ],
    )


def high_policy():
    return ExecutionPolicy(
        policy_id="high", risk_level="high",
        budget=ExecutionBudget(max_steps=8, max_tool_calls=20, max_wall_time_seconds=120),
        retry=RetryPolicy(max_retries=0),
        tool_permissions=[
            ToolPermission("read_file", allow=True),
            ToolPermission("run_tests", allow=True, requires_sandbox=True),
            ToolPermission("push_fix", allow=True, requires_approval=True),
        ],
    )


class ToolPolicyEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = ToolPolicyEngine(dict(META))

    def test_low_risk_policy_forbids_high_risk_tool(self):
        decision = self.engine.authorize("reliability", "push_fix", {}, low_policy(), {})
        self.assertFalse(decision.allowed)

    def test_unauthorized_agent_rejected(self):
        decision = self.engine.authorize("reliability", "push_fix", {}, high_policy(), {})
        self.assertFalse(decision.allowed)  # allowed_agents only includes deployer

    def test_unknown_tool_denied(self):
        decision = self.engine.authorize("a", "nope", {}, high_policy(), {})
        self.assertFalse(decision.allowed)

    def test_approved_tool_requires_approval(self):
        decision = self.engine.authorize("deployer", "push_fix", {}, high_policy(), {})
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_tool_budget_exceeded(self):
        policy = ExecutionPolicy(
            policy_id="p", risk_level="low",
            budget=ExecutionBudget(max_steps=5, max_tool_calls=10, max_wall_time_seconds=60),
            retry=RetryPolicy(max_retries=0),
            tool_permissions=[
                ToolPermission("read_file", allow=True, max_calls=2),
            ],
        )
        decision = self.engine.authorize(
            "a", "read_file", {}, policy,
            {"tool_call_counts": {"read_file": 2}},
        )
        self.assertFalse(decision.allowed)


class GovernedRegistryTest(unittest.TestCase):
    def test_unapproved_tool_raises(self):
        registry = GovernedToolRegistry(
            TOOLS, low_policy(), ToolPolicyEngine(dict(META))
        )
        with self.assertRaises(ToolPermissionDenied):
            registry.invoke_as("reliability", "run_tests", {})

    def test_side_effect_failure_not_auto_retried(self):
        calls = {"n": 0}

        def flaky(**arguments):
            calls["n"] += 1
            raise RuntimeError("boom")

        meta = dict(META)
        reg_tools = [
            AgentTool("push_fix", "push", {}, flaky),
        ]
        engine = ToolPolicyEngine({"push_fix": meta["push_fix"]})
        registry = GovernedToolRegistry(reg_tools, high_policy(), engine)
        # push_fix is side-effecting and requires approval; wire up an
        # approving provider so the guard behaviour, not approval, is tested.
        registry.approval_provider = lambda _decision: True
        with self.assertRaises(RuntimeError):
            registry.invoke_as("deployer", "push_fix", {})
        # Same idempotency key must be blocked on replay.
        from evoagent.tools.invocation import UnknownInvocationError
        with self.assertRaises(UnknownInvocationError):
            registry.invoke_as("deployer", "push_fix", {})
        self.assertEqual(calls["n"], 1)

    def test_successful_read_is_audited(self):
        registry = GovernedToolRegistry(
            TOOLS, low_policy(), ToolPolicyEngine(dict(META))
        )
        value = registry.invoke_as("reliability", "read_file", {"path": "a.py"})
        self.assertEqual(value, {"path": "a.py"})
        self.assertEqual(len(registry.audit.entries()), 1)
        self.assertEqual(registry.audit.entries()[0].status, "succeeded")


if __name__ == "__main__":
    unittest.main()