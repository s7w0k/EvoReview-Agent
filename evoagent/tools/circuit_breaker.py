"""Per-tool circuit breaker to stop hammering a repeatedly failing tool.

States are ``CLOSED`` (normal), ``OPEN`` (refusing calls) and ``HALF_OPEN``
(probing after the cooldown).  A tool that times out the configured consecutive
times opens the breaker for the cooldown window.
"""
import threading
import time


class CircuitOpenError(RuntimeError):
    """The tool circuit breaker is open and refused the call."""


class CircuitBreaker:
    """Track failure counts per tool and open/close accordingly."""

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        now: float = None,
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1.0, cooldown_seconds)
        self._now = now or time.monotonic
        self._lock = threading.RLock()
        self._state: dict = {}  # tool -> "closed"|"open"|"half_open"

    def _get(self, tool: str) -> dict:
        with self._lock:
            return self._state.setdefault(tool, {
                "state": "closed", "failure_count": 0,
                "open_until": 0.0, "last_failure_at": 0.0,
            })

    def state(self, tool: str) -> str:
        data = self._get(tool)
        now = self._now()
        if data["state"] == "open" and now >= data["open_until"]:
            data["state"] = "half_open"
        return data["state"]

    def allow(self, tool: str) -> bool:
        with self._lock:
            return self.state(tool) != "open"

    def check(self, tool: str) -> None:
        if not self.allow(tool):
            raise CircuitOpenError(
                "tool %s circuit is open; retrying after cooldown" % tool
            )

    def record_success(self, tool: str) -> None:
        with self._lock:
            data = self._get(tool)
            data["state"] = "closed"
            data["failure_count"] = 0

    def record_timeout(self, tool: str) -> None:
        self._failure(tool, kind="timeout")

    def record_failure(self, tool: str) -> None:
        self._failure(tool, kind="failure")

    def _failure(self, tool: str, kind: str) -> None:
        with self._lock:
            data = self._get(tool)
            data["last_failure_at"] = self._now()
            data["failure_count"] += 1
            if data["state"] == "half_open" or data["failure_count"] >= self.failure_threshold:
                data["state"] = "open"
                data["open_until"] = self._now() + self.cooldown_seconds

    def reset(self) -> None:
        with self._lock:
            self._state.clear()

    def snapshot(self) -> dict:
        with self._lock:
            return {tool: dict(data) for tool, data in self._state.items()}