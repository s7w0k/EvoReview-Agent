"""A2A resilience primitives (Phase 7): RetryPolicy, CircuitBreaker,
FallbackChain.

Retry only transient errors.  Circuit breaker rides to ``OPEN`` after repeated
failures, ``HALF_OPEN`` after a cooldown, and returns to ``CLOSED`` on a probe
success.  Fallback chains let caller prefer a backup Remote agent and finally a
local reviewer.
"""
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

from .errors import A2AError, A2ACircuitOpenError

#: Error classes that are safe to retry.
RETRYABLE = (A2AError,)


def is_retryable(error: Exception) -> bool:
    cls = type(error)
    return any(name in cls.__mro__.__str__() for name in ()) or _walks_retryable(error)


def _walks_retryable(error: Exception) -> bool:
    node = error
    while node is not None:
        base = getattr(node, "retryable", False)
        if base:
            return True
        node = getattr(node, "__cause__", None)
    return False


class RetryPolicy:
    """Retry transient A2A failures up to ``max_attempts`` total attempts.

    Args:
        max_attempts: total attempts (>=1, includes the initial one).
        delay_seconds: base backoff between attempts.
        backoff_factor: exponential multiplier per attempt (>=1.0).
    """

    def __init__(self, max_attempts: int = 3, delay_seconds: float = 0.0,
                 backoff_factor: float = 1.0):
        self.max_attempts = max(max_attempts, 1)
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.backoff_factor = max(1.0, float(backoff_factor))

    def should_retry(self, attempt: int, error: Exception) -> bool:
        if attempt >= self.max_attempts:
            return False
        return _walks_retryable(error)

    def sleep_for(self, attempt: int) -> float:
        delay = self.delay_seconds * (self.backoff_factor ** max(0, attempt - 1))
        if delay > 0:
            time.sleep(delay)
        return delay

    def run(
        self, func: Callable[[], Any],
        on_retry: Optional[Callable[[int, Exception], None]] = None,
    ) -> Any:
        """Call ``func`` under the retry policy, returning the success value."""
        attempt = 1
        while True:
            try:
                return func()
            except Exception as exc:  # noqa: BLE001 - classification decides
                if not self.should_retry(attempt, exc):
                    raise
                self.sleep_for(attempt)
                attempt += 1
                if on_retry:
                    on_retry(attempt, exc)


class CircuitBreaker:
    """Simple thread-safe failure-threshold breaker.

    States: ``CLOSED`` -> ``OPEN`` (after ``failure_threshold`` failures) ->
    ``HALF_OPEN`` (after ``cooldown_seconds``) -> ``CLOSED`` on success.
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 1.0,
                 name: str = ""):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.name = name
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        with self._lock:
            elapsed = time.monotonic() - self._opened_at
            if self._state == self.OPEN and elapsed >= self.cooldown_seconds:
                return self.HALF_OPEN
            return self._state

    def allow(self) -> bool:
        state = self.state
        return state == self.CLOSED or state == self.HALF_OPEN

    def _reject(self) -> A2ACircuitOpenError:
        return A2ACircuitOpenError(
            "circuit %r is %s" % (self.name or "", self.state),
            target_agent=self.name,
        )

    def call(self, func: Callable[[], Any]) -> Any:
        """Run ``func`` guarded by the breaker.

        Raises :class:`A2ACircuitOpenError` when the circuit is open.
        """
        if not self.allow():
            raise self._reject()
        try:
            result = func()
        except Exception:
            self._record_failure()
            raise
        self._record_success()
        return result

    def _record_failure(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                self._opened_at = now
                self._failures = 1
                return
            if self._state == self.OPEN:
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = now

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = self.CLOSED

    def reset(self) -> None:
        with self._lock:
            self._state = self.CLOSED
            self._failures = 0
            self._opened_at = 0.0


class FallbackChain:
    """Try a sequence of suppliers until one yields a non-failure value.

    Each supplier is ``Callable[[], Any]``.  Identity failures (schema /
    auth / circuit open) raise immediately; transient failures advance the
    chain and, if exhausted, re-raise the last error.
    """

    def __init__(self, on_fallback: Optional[Callable[[str, str], None]] = None):
        self._on_fallback = on_fallback

    def run(self, providers: List[Callable[[], Any]], *,
            identity_errors: Tuple[type, ...] = ()) -> Any:
        if not providers:
            raise A2AError("fallback chain is empty")
        last_error: Optional[Exception] = None
        for index, provider in enumerate(providers):
            try:
                return provider()
            except identity_errors:  # fail fast on contract/auth errors
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if index + 1 < len(providers) and self._on_fallback:
                    self._on_fallback("remote->backup", type(exc).__name__)
        if last_error is not None:
            raise last_error
        raise A2AError("fallback chain produced no result")


__all__ = ["RetryPolicy", "CircuitBreaker", "FallbackChain", "is_retryable"]