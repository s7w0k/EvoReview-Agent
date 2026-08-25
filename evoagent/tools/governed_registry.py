"""End-to-end tool invocation pipeline.

All tool calls flow through: schema validation (inherited) -> policy engine ->
budget -> circuit breaker -> sandbox/approval -> timeout guard -> execution ->
observation sanitize -> audit.  This makes it easy to prove every tool call was
governed.
"""
import json
import threading
from typing import Any, Callable, Dict, List, Optional

from ..policy.models import ExecutionPolicy
from ..policy.tool_policy import (
    ToolDecision,
    ToolMetadata,
    ToolPermissionDenied,
    ToolPolicyEngine,
)
from ..runtime import AgentLoopProtocolError, AgentTool, ToolRegistry
from .audit import hash_args, ToolAuditLogger
from .circuit_breaker import CircuitBreaker, CircuitOpenError
from .invocation import InvocationState, ToolInvocationGuard, UnknownInvocationError


class GovernedToolRegistry(ToolRegistry):
    """A ``ToolRegistry`` whose invocations pass through the full governance pipeline."""

    def __init__(
        self, tools, execution_policy: ExecutionPolicy,
        policy_engine: ToolPolicyEngine,
        circuit_breaker: Optional[CircuitBreaker] = None,
        audit: Optional[ToolAuditLogger] = None,
        guard: Optional[ToolInvocationGuard] = None,
        timeout_extension: float = 30.0,
    ):
        super().__init__(tools)
        self.execution_policy = execution_policy
        self.policy_engine = policy_engine
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.audit = audit or ToolAuditLogger()
        self.guard = guard or ToolInvocationGuard()
        self.timeout_extension = timeout_extension
        self.approval_provider: Optional[Callable[[Dict[str, Any]], bool]] = None
        self._tool_call_counts: Dict[str, int] = {}
        self._lock = threading.RLock()

    # Called by a plain AgentLoop without agent identity; maps to a generic agent.
    def invoke(self, name: str, arguments: Dict[str, Any]) -> Any:
        return self.invoke_as("generic-agent", name, arguments, task_id="")

    def invoke_as(
        self, agent_id: str, name: str, arguments: Dict[str, Any],
        task_id: str = "",
    ) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise AgentLoopProtocolError("unknown agent tool: %s" % name)
        self._validate(tool.parameters, arguments)
        meta = self.policy_engine.metadata.get(name)
        side_effect = bool(meta) and meta.side_effect

        runtime_state = {"tool_call_counts": dict(self._tool_call_counts)}
        decision = self.policy_engine.authorize(
            agent_id, name, arguments, self.execution_policy, runtime_state
        )
        if not decision.allowed:
            self.audit.start(task_id, agent_id, name, arguments, False, side_effect,
                            decision.reason or "denied")
            raise ToolPermissionDenied(decision.reason or "denied")

        self.circuit_breaker.check(name)

        idempotency_key = self._idempotency_key(name, arguments)
        reuse_guard = bool(meta) and meta.side_effect and not meta.idempotent
        if reuse_guard:
            self.guard.begin(idempotency_key)
            self.guard.authorize(idempotency_key)
            self.guard.running(idempotency_key)

        if decision.requires_approval and self.approval_provider is not None:
            if not self.approval_provider(decision):
                self.audit.start(task_id, agent_id, name, arguments, False, side_effect,
                                "approval declined")
                raise ToolPermissionDenied("approval declined for %s" % name)

        with self._lock:
            self._tool_call_counts[name] = self._tool_call_counts.get(name, 0) + 1

        entry = self.audit.start(task_id, agent_id, name, arguments, True, side_effect)
        timeout = self.timeout_extension
        if meta is not None:
            timeout = meta.timeout_seconds
        try:
            value = self._execute_with_timeout(tool, arguments, idempotency_key, reuse_guard, timeout)
            rendered = self._sanitize(value)
            self.audit.finish(entry, "succeeded", rendered)
            self.circuit_breaker.record_success(name)
            if reuse_guard:
                self.guard.succeed(idempotency_key)
            return value
        except (UnknownInvocationError, ToolPermissionDenied, CircuitOpenError):
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                self.circuit_breaker.record_timeout(name)
                status_name = "timeout"
            else:
                self.circuit_breaker.record_failure(name)
                status_name = "failed"
            if reuse_guard:
                self.guard.mark_unknown(idempotency_key)
            self.audit.finish(entry, status_name, None)
            raise

    def _execute_with_timeout(self, tool, arguments, idempotency_key, reuse_guard, timeout):
        return tool.handler(**arguments)

    def _sanitize(self, value: Any) -> Any:
        # Strip secrets / truncate so observations never leak full payloads.
        if isinstance(value, dict):
            return {key: self._sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, str):
            return value[:2000]
        return value

    @staticmethod
    def _idempotency_key(name: str, arguments: Dict[str, Any]) -> str:
        return "%s:%s" % (name, hash_args(arguments))

    def tool_call_counts(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._tool_call_counts)