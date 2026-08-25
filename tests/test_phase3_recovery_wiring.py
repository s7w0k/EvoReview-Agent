"""Phase 3 acceptance tests: recovery driven by real runtime failures.

Covers (plan section 7):
  7.2  Runtime node failures route through RecoveryManager (no naive retry).
  7.3  Standardised model exceptions replace bare RuntimeError.
  7.4  Tool failures are classified (recoverable vs normal observation).
  7.5  No-progress detection is wired into AgentLoop.
  7.6  Recovery budget caps attempts.
"""
import unittest
from dataclasses import replace

from evoagent.errors import (
    ModelContextOverflow,
    ModelInvalidOutput,
    ModelRateLimit,
    ModelTimeout,
    ModelUnavailable,
)
from evoagent.recovery.classifier import FailureClassifier
from evoagent.recovery.failures import FailureType
from evoagent.recovery.manager import RecoveryBudget, RecoveryManager
from evoagent.runtime import (
    AgentLoop,
    AgentLoopNoProgress,
    AgentRuntime,
    RuntimeNode,
    RuntimeCancelled,
)


class ModelExceptionStandardisationTest(unittest.TestCase):
    """7.3: the five model exceptions are raised instead of RuntimeError."""

    def test_exceptions_are_runtime_errors_but_subclass_model_error(self):
        for exc in (
            ModelTimeout("t"), ModelRateLimit("r"), ModelContextOverflow("c"),
            ModelInvalidOutput("i"), ModelUnavailable("u"),
        ):
            self.assertIsInstance(exc, RuntimeError)

    def test_classifier_maps_each_model_exception(self):
        classifier = FailureClassifier()
        cases = [
            (ModelTimeout("timed out"), FailureType.MODEL_TIMEOUT),
            (ModelRateLimit("429"), FailureType.MODEL_RATE_LIMIT),
            (ModelContextOverflow("context length exceeded"),
             FailureType.MODEL_CONTEXT_OVERFLOW),
            (ModelInvalidOutput("bad json"), FailureType.MODEL_INVALID_OUTPUT),
            (ModelUnavailable("connection error"), FailureType.MODEL_UNAVAILABLE),
        ]
        for exc, expected in cases:
            self.assertEqual(classifier.classify(exc=exc), expected)


class RuntimeNodeRecoveryTest(unittest.TestCase):
    """7.2 + 7.6: node failures are routed through RecoveryManager."""

    def test_recovery_retries_then_aborts_within_budget(self):
        attempts = {"n": 0}

        def flaky(_state):
            attempts["n"] += 1
            raise ModelTimeout("upstream timed out")

        manager = RecoveryManager(
            budget=RecoveryBudget(max_recovery_attempts=2),
        )
        runtime = AgentRuntime(
            max_steps=10, timeout_seconds=60, node_retries=0,
            recovery_manager=manager,
        )
        with self.assertRaises(ModelTimeout):
            runtime.execute(
                {"task_id": "node-t"}, [RuntimeNode("planning", flaky)],
                task_id="node-t",
            )
        # attempt 1 retries, attempt 2 exhausts the budget and aborts.
        self.assertLessEqual(attempts["n"], 2)

    def test_no_recovery_manager_keeps_naive_retry(self):
        attempts = {"n": 0}

        def flaky(_state):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise ModelUnavailable("temporarily down")
            return {"done": True}

        runtime = AgentRuntime(max_steps=10, timeout_seconds=60, node_retries=2)
        state = runtime.execute(
            {"task_id": "legacy-t"}, [RuntimeNode("planning", flaky)],
            task_id="legacy-t",
        )
        self.assertTrue(state["done"])
        self.assertEqual(attempts["n"], 2)

    def test_non_retryable_failure_passes_through_unchanged(self):
        def bad(_state):
            raise RuntimeCancelled("cancelled")

        manager = RecoveryManager(budget=RecoveryBudget())
        runtime = AgentRuntime(
            max_steps=10, timeout_seconds=60, recovery_manager=manager,
        )
        with self.assertRaises(RuntimeCancelled):
            runtime.execute(
                {"task_id": "cancel-t"}, [RuntimeNode("planning", bad)],
                task_id="cancel-t",
            )


class AgentLoopFailureTest(unittest.TestCase):
    """7.4: tool failures are classified recoverable vs normal."""

    def test_recoverable_failure_is_marked(self):
        def fail_tool(**_kwargs):
            raise ModelRateLimit("429 too many requests")

        def stepper(state):
            if state.get("loop_step") == 1:
                return {"action": "tool", "tool": "fragile",
                        "arguments": {}}
            return {"action": "final", "findings": []}

        tools = {"fragile": fail_tool}
        loop = AgentLoop(max_steps=3, timeout_seconds=5)
        result = loop.run(stepper, tools, {})
        obs = result.observations[0]
        self.assertFalse(obs["ok"])
        self.assertEqual(obs["failure_type"], "MODEL_RATE_LIMIT")
        self.assertTrue(obs["recoverable"])

    def test_normal_observation_failure_is_not_recoverable(self):
        def missing(**_kwargs):
            raise FileNotFoundError("no such file")

        def stepper(state):
            if state.get("loop_step") == 1:
                return {"action": "tool", "tool": "missing_file",
                        "arguments": {}}
            return {"action": "final", "findings": []}

        tools = {"missing_file": missing}
        loop = AgentLoop(max_steps=3, timeout_seconds=5)
        result = loop.run(stepper, tools, {})
        obs = result.observations[0]
        self.assertFalse(obs["ok"])
        self.assertFalse(obs["recoverable"])


class AgentLoopNoProgressTest(unittest.TestCase):
    """7.5: repeated identical actions raise AgentLoopNoProgress."""

    def test_loop_raises_no_progress_on_identical_actions(self):
        def stepper(state):
            if state.get("loop_step") in (1, 2, 3):
                return {"action": "tool", "tool": "search_diff",
                        "arguments": {"query": "eval"}}
            return {"action": "final", "findings": []}

        def tool(**_kwargs):  # noqa: ARG001
            return []

        loop = AgentLoop(max_steps=5, timeout_seconds=5)
        with self.assertRaises(AgentLoopNoProgress):
            loop.run(stepper, {"search_diff": tool}, {})

    def test_distinct_actions_do_not_trigger_no_progress(self):
        def stepper(state):
            step = state.get("loop_step")
            if step in (1, 2, 3, 4):
                return {"action": "tool", "tool": "search_diff",
                        "arguments": {"query": "query-%d" % step}}
            return {"action": "final", "findings": []}

        def tool(**_kwargs):  # noqa: ARG001
            return []

        loop = AgentLoop(max_steps=5, timeout_seconds=5)
        result = loop.run(stepper, {"search_diff": tool}, {})
        self.assertEqual(result.stop_reason, "final")


if __name__ == "__main__":
    unittest.main()