import unittest

from evoagent.policy.models import (
    ExecutionBudget,
    ExecutionPolicy,
    RetryPolicy,
    ToolPermission,
)
from evoagent.runtime import (
    AgentLoop,
    AgentRuntime,
    AgentTool,
    RuntimeBudgetExceeded,
    RuntimeCancelled,
    RuntimeNode,
)


def echo_handler(**arguments):
    return arguments


def failing_handler(**arguments):
    raise RuntimeError("boom")


TOOLS = [
    AgentTool("read_file", "read a file", {"properties": {"path": {"type": "string"}}}, echo_handler),
    AgentTool("run_tests", "run tests", {}, failing_handler),
]


class RuntimePolicyEnforcementTest(unittest.TestCase):
    def test_step_budget_strictly_enforced(self):
        runtime = AgentRuntime(
            execution_policy=ExecutionPolicy(
                policy_id="t",
                budget=ExecutionBudget(max_steps=2, max_tool_calls=5, max_wall_time_seconds=60),
                retry=RetryPolicy(max_retries=0),
            )
        )
        with self.assertRaises(RuntimeBudgetExceeded):
            runtime.execute({}, [
                RuntimeNode("a", lambda s: {"x": 1}),
                RuntimeNode("b", lambda s: {"x": 2}),
                RuntimeNode("c", lambda s: {"x": 3}),
            ])

    def test_tool_budget_exceeded_graceful_stop(self):
        loop = AgentLoop(
            execution_policy=ExecutionPolicy(
                policy_id="t",
                budget=ExecutionBudget(max_steps=10, max_tool_calls=3, max_wall_time_seconds=60),
            )
        )
        from evoagent.runtime import ToolRegistry

        def stepper(state):
            return {"action": "tool", "tool": "read_file", "arguments": {"path": "x.txt"}}

        with self.assertRaises(RuntimeBudgetExceeded):
            loop.run(stepper, ToolRegistry(TOOLS), {})

    def test_invalid_policy_rejected_at_startup(self):
        with self.assertRaises(ValueError):
            AgentRuntime(execution_policy=ExecutionPolicy(
                policy_id="bad",
                budget=ExecutionBudget(max_steps=0, max_tool_calls=5),
            ))

    def test_retry_policy_wired_from_execution_policy(self):
        calls = {"n": 0}

        def handler(state):
            calls["n"] += 1
            raise RuntimeError("retry me")

        runtime = AgentRuntime(execution_policy=ExecutionPolicy(
            policy_id="t",
            budget=ExecutionBudget(max_steps=5, max_tool_calls=5, max_wall_time_seconds=60),
            retry=RetryPolicy(max_retries=2),
        ))
        with self.assertRaises(RuntimeError):
            runtime.execute({}, [RuntimeNode("n", handler)])
        self.assertEqual(calls["n"], 3)

    def test_cancellation_still_works_with_policy(self):
        runtime = AgentRuntime(execution_policy=ExecutionPolicy(
            policy_id="t",
            budget=ExecutionBudget(max_steps=5, max_tool_calls=5, max_wall_time_seconds=60),
            retry=RetryPolicy(max_retries=0),
        ))
        with self.assertRaises(RuntimeCancelled):
            runtime.execute({}, [RuntimeNode("a", lambda s: {"x": 1})],
                            cancel_check=lambda: True)


if __name__ == "__main__":
    unittest.main()