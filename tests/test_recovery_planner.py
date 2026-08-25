import unittest

from evoagent.policy.models import (
    AgentPolicy,
    ExecutionBudget,
    ExecutionPolicy,
    RetryPolicy,
)
from evoagent.recovery.executor import RecoveryExecutor
from evoagent.recovery.failures import FailureEvent, FailureType, RecoveryAction
from evoagent.recovery.planner import RecoveryPlanner


def policy():
    return ExecutionPolicy(
        policy_id="p", risk_level="high",
        budget=ExecutionBudget(max_steps=10, max_tool_calls=20, max_wall_time_seconds=120),
        retry=RetryPolicy(max_retries=3, exponential_backoff=True),
        agents=AgentPolicy(
            enabled_agents=["security", "reliability"],
            fallback_agents=["reliability", "semantic"],
        ),
    )


class RecoveryPlannerTest(unittest.TestCase):
    def setUp(self):
        self.planner = RecoveryPlanner()

    def test_model_timeout_first_retries_with_backoff(self):
        action = self.planner.plan(
            FailureType.MODEL_TIMEOUT, policy(), {"attempt": 1}
        )
        self.assertEqual(action, RecoveryAction.RETRY_WITH_BACKOFF)

    def test_model_timeout_prones_to_switch_model(self):
        action = self.planner.plan(
            FailureType.MODEL_TIMEOUT, policy(), {"attempt": 2}
        )
        self.assertEqual(action, RecoveryAction.SWITCH_MODEL)

    def test_context_overflow_compresses_first(self):
        action = self.planner.plan(
            FailureType.MODEL_CONTEXT_OVERFLOW, policy(), {"attempt": 1}
        )
        self.assertEqual(action, RecoveryAction.COMPRESS_CONTEXT)

    def test_no_progress_replans(self):
        action = self.planner.plan(
            FailureType.AGENT_NO_PROGRESS, policy(), {"attempt": 1}
        )
        self.assertEqual(action, RecoveryAction.REPLAN)

    def test_budget_exceeded_aborts_never_retries(self):
        action = self.planner.plan(
            FailureType.BUDGET_EXCEEDED, policy(), {"attempt": 1}
        )
        self.assertEqual(action, RecoveryAction.ABORT)

    def test_side_effect_unknown_goes_human(self):
        action = self.planner.plan(
            FailureType.TOOL_SIDE_EFFECT_UNKNOWN, policy(), {"attempt": 1},
            context={"side_effect_unknown": True},
        )
        self.assertEqual(action, RecoveryAction.HUMAN_REVIEW)


class RecoveryExecutorTest(unittest.TestCase):
    def test_backoff_with_exponential(self):
        executor = RecoveryExecutor()
        result = executor.execute(
            FailureEvent(task_id="t", failure_type=FailureType.MODEL_TIMEOUT,
                         recovery_action=RecoveryAction.RETRY_WITH_BACKOFF, attempt=3),
            policy(), {},
        )
        self.assertEqual(result["backoff_seconds"], 1.0 * 2 ** 2)

    def test_fallback_agent(self):
        executor = RecoveryExecutor()
        result = executor.execute(
            FailureEvent(failure_type=FailureType.AGENT_NO_PROGRESS,
                         recovery_action=RecoveryAction.FALLBACK_AGENT),
            policy(), {},
        )
        self.assertEqual(result["agent"], policy().agents.fallback_agents[0])

    def test_compensate_without_handler(self):
        executor = RecoveryExecutor()
        result = executor.execute(
            FailureEvent(failure_type=FailureType.TOOL_SIDE_EFFECT_UNKNOWN,
                         recovery_action=RecoveryAction.COMPENSATE,
                         context={"tool": "push_fix"}),
            policy(), {},
        )
        self.assertFalse(result["compensated"])


if __name__ == "__main__":
    unittest.main()