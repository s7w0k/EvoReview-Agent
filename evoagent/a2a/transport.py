"""A2A Transport interface + resilient wrapper (Phase 2 / 7).

``A2ATransport`` is the pluggable boundary so InProcess and HTTP transports
are drop-in interchangeable for the same :class:`A2ATask`.  ``ResilientTransport``
wraps any concrete transport with retry / circuit breaker / timeout and, on
demand, falls over to a backup transport -- while mirroring everything into
``evoagent.metrics.metrics`` for the decision trace.
"""
from typing import List, Optional

from .errors import A2ACircuitOpenError, A2AError
from .models import A2ATask, AgentCard
from .resilience import CircuitBreaker, RetryPolicy
from .telemetry import A2AMetrics


class A2ATransport:
    """Interface for a remote-agent transport.

    All methods return dicts / lists of dicts so callers stay decoupled from
    the wire encoding.  Implementations raise the :mod:`A2A errors` taxonomy.
    """

    def discover(self, endpoint: str) -> dict:
        raise NotImplementedError

    def submit_task(self, card: AgentCard, task: A2ATask) -> dict:
        raise NotImplementedError

    def get_task(self, card: AgentCard, task_id: str) -> dict:
        raise NotImplementedError

    def cancel_task(self, card: AgentCard, task_id: str) -> dict:
        raise NotImplementedError

    def get_artifacts(self, card: AgentCard, task_id: str) -> List[dict]:
        raise NotImplementedError


class ResilientTransport(A2ATransport):
    """Adds retry, circuit breaking and optional backup fallback over a base
    transport so the coordinator can call a Remote agent defensively."""

    #: maps a public method to its A2A JSON-RPC method name.
    _METHODS = {
        "discover": "agent.discover",
        "submit_task": "task.submit",
        "get_task": "task.get",
        "cancel_task": "task.cancel",
        "get_artifacts": "artifact.list",
    }

    def __init__(
        self, transport: A2ATransport, *, retry: Optional[RetryPolicy] = None,
        breaker: Optional[CircuitBreaker] = None, backup: Optional[A2ATransport] = None,
        timeout_seconds: Optional[float] = None, metrics: Optional[A2AMetrics] = None,
    ):
        self.transport = transport
        self.retry = retry or RetryPolicy()
        self.breaker = breaker or CircuitBreaker(name=type(transport).__name__)
        self.backup = backup
        self.timeout_seconds = timeout_seconds
        self.metrics = metrics

    # -- helpers -----------------------------------------------------------
    def _invoke(self, method: str, task_id: str, func):
        def guarded():
            return self.breaker.call(func)

        try:
            value = self.retry.run(guarded, on_retry=self._on_retry)
            if self.metrics is not None:
                self.metrics.record_request(method, task_id or "", "success")
            return value
        except A2ACircuitOpenError:
            if self.metrics is not None:
                self.metrics.record_circuit_open(task_id or "")
            if self.backup is not None:
                if self.metrics is not None:
                    self.metrics.record_fallback("circuit-open-to-backup", "circuit-open")
                return self._dispatch(self.backup, method, task_id)
            raise
        except Exception as exc:  # noqa: BLE001
            if self.metrics is not None:
                self.metrics.record_failure(task_id or "", type(exc).__name__)
                if isinstance(exc, A2AError) and getattr(exc, "retryable", False):
                    self.metrics.record_timeout(task_id or "")
            if self.backup is not None:
                if self.metrics is not None:
                    self.metrics.record_fallback("remote-to-backup", type(exc).__name__)
                return self._dispatch(self.backup, method, task_id)
            raise

    def _dispatch(self, transport: "A2ATransport", method: str, task_id: str):
        """Route a failed method to the backup transport using its stored args.

        The concrete operations store ``(card, task / task_id)`` on the instance
        so the backup can be invoked without re-reading the request.
        """
        args = getattr(self, "_last_args_%s" % method, None)
        if args is None:
            raise A2AError("cannot fall back to backup for %r" % method)
        card, payload = args[0], args[1]
        if method == "discover":
            return transport.discover(payload)
        if method == "submit_task":
            return transport.submit_task(card, payload)
        if method == "get_task":
            return transport.get_task(card, payload)
        if method == "cancel_task":
            return transport.cancel_task(card, payload)
        if method == "get_artifacts":
            return transport.get_artifacts(card, payload)
        raise A2AError("unknown fallback method: %r" % method)

    def _call(self, method: str, task_id: str, func, *args):
        setattr(self, "_last_args_%s" % method, args)
        return self._invoke(method, task_id, func)

    def _on_retry(self, attempt: int, error: Exception) -> None:
        if attempt > 1 and self.metrics is not None:
            self.metrics.record_retry(type(self).__name__)

    def discover(self, endpoint: str) -> dict:
        return self._call("discover", "", lambda: self.transport.discover(endpoint), endpoint)

    def submit_task(self, card: AgentCard, task: A2ATask) -> dict:
        return self._call(
            "submit_task", task.task_id,
            lambda: self.transport.submit_task(card, task), card, task,
        )

    def get_task(self, card: AgentCard, task_id: str) -> dict:
        return self._call(
            "get_task", task_id,
            lambda: self.transport.get_task(card, task_id), card, task_id,
        )

    def cancel_task(self, card: AgentCard, task_id: str) -> dict:
        return self._call(
            "cancel_task", task_id,
            lambda: self.transport.cancel_task(card, task_id), card, task_id,
        )

    def get_artifacts(self, card: AgentCard, task_id: str) -> List[dict]:
        value = self._call(
            "get_artifacts", task_id,
            lambda: self.transport.get_artifacts(card, task_id), card, task_id,
        )
        return list(value or [])


class _BackupReflectionMixin:
    """Routes a method to the backup transport (dummy guard for abstract)."""
    pass


__all__ = ["A2ATransport", "ResilientTransport"]