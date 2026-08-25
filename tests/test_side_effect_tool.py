import unittest

from evoagent.policy.models import (
    ExecutionBudget,
    ExecutionPolicy,
    ToolPermission,
)
from evoagent.policy.tool_policy import ToolMetadata, ToolPolicyEngine
from evoagent.runtime import AgentTool
from evoagent.tools.invocation import InvocationState, ToolInvocationGuard
from evoagent.tools.governed_registry import GovernedToolRegistry


def policy(max_tool_calls=10):
    return ExecutionPolicy(
        policy_id="p", risk_level="high",
        budget=ExecutionBudget(max_steps=8, max_tool_calls=max_tool_calls),
        tool_permissions=[
            ToolPermission("read_file", allow=True),
            ToolPermission("write_comment", allow=True, requires_approval=True),
        ],
    )


READ = ToolMetadata("read_file", risk_level="low")
WRITE = ToolMetadata(
    "write_comment", risk_level="medium", side_effect=True, idempotent=False,
    allowed_agents=["deployer"],
)


class SideEffectToolTest(unittest.TestCase):
    def test_idempotency_key_blocks_replay_after_failure(self):
        calls = {"n": 0}

        def failing(**arguments):
            calls["n"] += 1
            raise RuntimeError("nope")

        import evoagent.tools.invocation as inv
        engine = ToolPolicyEngine({"write_comment": WRITE})
        registry = GovernedToolRegistry(
            [AgentTool(
                "write_comment", "w",
                {"properties": {"text": {"type": "string"}}},
                failing,
            )],
            policy(), engine,
        )
        # write_comment requires human approval; approve so the idempotency
        # guard (not approval) is what is under test.
        registry.approval_provider = lambda _decision: True
        with self.assertRaises(RuntimeError):
            registry.invoke_as("deployer", "write_comment", {"text": "x"})
        from evoagent.tools.invocation import UnknownInvocationError
        with self.assertRaises(UnknownInvocationError):
            registry.invoke_as("deployer", "write_comment", {"text": "x"})
        self.assertEqual(calls["n"], 1)

    def test_guard_state_machine(self):
        guard = ToolInvocationGuard()
        self.assertEqual(guard.begin("k1"), InvocationState.REQUESTED)
        guard.authorize("k1")
        guard.running("k1")
        guard.succeed("k1")
        self.assertEqual(guard.state("k1"), InvocationState.SUCCEEDED)
        # SUCCEEDED is a terminal state but replay is allowed to return the recorded
        # outcome; only FAILED/UNKNOWN block replay in the guard.
        self.assertEqual(guard.begin("k1"), InvocationState.SUCCEEDED)

    def test_failed_state_blocks_replay(self):
        guard = ToolInvocationGuard()
        guard.begin("k1")
        guard.fail("k1")
        from evoagent.tools.invocation import UnknownInvocationError
        with self.assertRaises(UnknownInvocationError):
            guard.begin("k1")


if __name__ == "__main__":
    unittest.main()