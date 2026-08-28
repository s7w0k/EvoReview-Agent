"""Phase 7 acceptance: only transient errors are retried; contract errors
(schema / protocol / auth) fail fast without retries."""
import unittest

from evoagent.a2a.errors import (
    A2AProtocolError, A2AUnavailableError, A2AUnauthorizedError,
)
from evoagent.a2a.models import A2ATask, AgentCard
from evoagent.a2a.resilience import RetryPolicy
from evoagent.a2a.transport import A2ATransport, ResilientTransport

CARD = AgentCard(agent_id="a", name="a", endpoint="x", protocol_version="v1")


def _task():
    return A2ATask(task_id="t", assignment_id="A", sender="s", recipient="a",
                   task_type="review-assignment", input={"diff": ""})


class _Flaky(A2ATransport):
    def __init__(self, failures, error_cls):
        self.failures = failures
        self.error_cls = error_cls
        self.calls = 0

    def submit_task(self, card, task):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error_cls("injected", target_agent="a")
        return {"status": "COMPLETED", "task_id": task.task_id}


class RetryTest(unittest.TestCase):
    def test_transient_error_is_retried_and_succeeds(self):
        flaky = _Flaky(2, A2AUnavailableError)
        resilient = ResilientTransport(flaky, retry=RetryPolicy(max_attempts=3))
        result = resilient.submit_task(CARD, _task())
        self.assertEqual(result["status"], "COMPLETED")
        self.assertGreater(flaky.calls, 1)

    def test_retries_exhausted_raises(self):
        flaky = _Flaky(99, A2AUnavailableError)
        resilient = ResilientTransport(flaky, retry=RetryPolicy(max_attempts=2))
        with self.assertRaises(A2AUnavailableError):
            resilient.submit_task(CARD, _task())
        self.assertEqual(flaky.calls, 2)

    def test_identity_error_is_not_retried(self):
        for cls in (A2AProtocolError, A2AUnauthorizedError):
            flaky = _Flaky(99, cls)
            resilient = ResilientTransport(flaky, retry=RetryPolicy(max_attempts=3))
            with self.assertRaises(cls):
                resilient.submit_task(CARD, _task())
            self.assertEqual(flaky.calls, 1, "%s must not be retried" % cls)


class CircuitBreakerTest(unittest.TestCase):
    def test_opens_and_recovers(self):
        from evoagent.a2a.resilience import CircuitBreaker
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)

        def fail():
            raise A2AUnavailableError("boom", target_agent="a")

        for _ in range(2):
            with self.assertRaises(A2AUnavailableError):
                breaker.call(fail)
        self.assertEqual(breaker.state, "OPEN")
        from evoagent.a2a.errors import A2ACircuitOpenError
        with self.assertRaises(A2ACircuitOpenError):
            breaker.call(fail)
        # after cooldown it is HALF_OPEN and a success closes it
        import time as _t
        _t.sleep(0.02)
        self.assertEqual(breaker.call(lambda: "ok"), "ok")
        self.assertEqual(breaker.state, "CLOSED")


if __name__ == "__main__":
    unittest.main()