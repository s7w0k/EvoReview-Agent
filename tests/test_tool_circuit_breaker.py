import time
import unittest

from evoagent.tools.circuit_breaker import CircuitBreaker, CircuitOpenError


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class CircuitBreakerTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.breaker = CircuitBreaker(
            failure_threshold=3, cooldown_seconds=10, now=self.clock
        )

    def test_closed_allows(self):
        self.assertTrue(self.breaker.allow("run_tests"))
        self.assertEqual(self.breaker.state("run_tests"), "closed")

    def test_opens_after_threshold(self):
        for _ in range(3):
            self.breaker.record_timeout("run_tests")
        with self.assertRaises(CircuitOpenError):
            self.breaker.check("run_tests")
        self.assertEqual(self.breaker.state("run_tests"), "open")

    def test_half_open_after_cooldown(self):
        for _ in range(3):
            self.breaker.record_failure("run_tests")
        # Open now.
        with self.assertRaises(CircuitOpenError):
            self.breaker.check("run_tests")
        # After cooldown a probe is allowed (half_open), then a failure re-opens it.
        self.clock.now = 11.0
        self.assertTrue(self.breaker.allow("run_tests"))
        self.assertEqual(self.breaker.state("run_tests"), "half_open")
        self.breaker.record_failure("run_tests")
        self.clock.now = 12.0
        with self.assertRaises(CircuitOpenError):
            self.breaker.check("run_tests")

    def test_success_closes(self):
        for _ in range(3):
            self.breaker.record_timeout("run_tests")
        self.breaker.record_success("run_tests")
        self.assertTrue(self.breaker.allow("run_tests"))


if __name__ == "__main__":
    unittest.main()