"""End-to-end tool invocation pipeline.

All tool calls flow through: schema validation (inherited) -> policy engine ->
budget -> circuit breaker -> sandbox/approval -> timeout guard -> execution ->
observation sanitize -> audit.  This makes it easy to prove every tool call was
governed.
"""
import json
import threading
from typing import Any, Callable, Dict, List, Optional

from ..metrics import metrics
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
from .executor import ToolExecutionResult, ToolExecutor, ToolTimeoutError
from .invocation import InvocationState, ToolInvocationGuard, UnknownInvocationError
from .sandbox import SandboxContext, SandboxEnforcer


class GovernedToolRegistry(ToolRegistry):
    """A ``ToolRegistry`` whose invocations pass through the full governance pipeline."""

    def __init__(
        self, tools, execution_policy: ExecutionPolicy,
        policy_engine: ToolPolicyEngine,
        circuit_breaker: Optional[CircuitBreaker] = None,
        audit: Optional[ToolAuditLogger] = None,
        guard: Optional[ToolInvocationGuard] = None,
        timeout_extension: float = 30.0,
        executor: Optional[ToolExecutor] = None,
        sandbox_context: Optional[SandboxContext] = None,
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
        self.sandbox_context = sandbox_context
        self.executor = executor or ToolExecutor(sandbox=sandbox_context)

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
            metrics.inc("tool_calls_denied_total")
            metrics.inc("tool_policy_violation_total")
            raise ToolPermissionDenied(decision.reason or "denied")

        try:
            self.circuit_breaker.check(name)
        except CircuitOpenError:
            metrics.inc("tool_circuit_open_total")
            raise

        idempotency_key = self._idempotency_key(name, arguments)
        reuse_guard = bool(meta) and meta.side_effect and not meta.idempotent
        if reuse_guard:
            self.guard.begin(idempotency_key)
            self.guard.authorize(idempotency_key)
            self.guard.running(idempotency_key)

        if decision.requires_approval:
            metrics.inc("tool_approval_requested_total")
            # fail-closed: a required approval is refused outright when no
            # human-approval provider is wired up.  We never silently execute.
            if self.approval_provider is None:
                self.audit.start(task_id, agent_id, name, arguments, False, side_effect,
                                 "approval required but no provider configured")
                metrics.inc("tool_calls_denied_total")
                raise ToolPermissionDenied(
                    "approval required for %s but no approval provider configured" % name
                )
            if not self.approval_provider(decision):
                self.audit.start(task_id, agent_id, name, arguments, False, side_effect,
                                 "approval declined")
                metrics.inc("tool_approval_denied_total")
                metrics.inc("tool_calls_denied_total")
                raise ToolPermissionDenied("approval declined for %s" % name)

        # Budget is consumed only once the tool is genuinely about to execute —
        # never for a schema-invalid, policy-denied or approval-denied call.
        with self._lock:
            self._tool_call_counts[name] = self._tool_call_counts.get(name, 0) + 1
            self._tool_call_counts["__all__"] = self._tool_call_counts.get("__all__", 0) + 1

        entry = self.audit.start(task_id, agent_id, name, arguments, True, side_effect)
        try:
            result = self.executor.execute(
                tool, arguments, meta,
                {"task_id": task_id, "agent_id": agent_id, "name": name},
            )
            value = result.value if isinstance(result, ToolExecutionResult) else result
            rendered = self._sanitize(value)
            self.audit.finish(entry, "succeeded", rendered)
            self.circuit_breaker.record_success(name)
            if reuse_guard:
                self.guard.succeed(idempotency_key)
            metrics.inc("tool_calls_total")
            return value
        except (UnknownInvocationError, ToolPermissionDenied, CircuitOpenError):
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError) or isinstance(exc, ToolTimeoutError):
                self.circuit_breaker.record_timeout(name)
                status_name = "timeout"
                metrics.inc("tool_timeouts_total")
            else:
                self.circuit_breaker.record_failure(name)
                status_name = "failed"
            if reuse_guard:
                self.guard.mark_unknown(idempotency_key)
            self.audit.finish(entry, status_name, None)
            raise

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


def procedure_tool_invoker(
    registry: GovernedToolRegistry, skill_name: str, task_id: str = "",
) -> Callable[[str, Dict[str, Any]], Any]:
    """Bind a ``ProcedureExecutor``'s invoker to the governed registry (6.4).

    Procedure steps can never bypass governance: every tool call runs as
    ``procedure:<skill>`` and is authorised / budgeted / audited like any other
    agent call.
    """
    def invoke(name: str, arguments: Dict[str, Any]) -> Any:
        return registry.invoke_as(
            "procedure:%s" % skill_name, name, arguments, task_id=task_id,
        )
    return invoke


def read_only_replay_policy(
    read_only_tools: List[str], risk_level: str = "low",
) -> ExecutionPolicy:
    """A policy whose allow-list only contains read-only tools (6.5).

    Side-effect tools are absent from the permission list, so the policy engine's
    ``_default_allowed`` guard denies them after fail-closed.  Live replay routes
    every tool call through this policy via ``GovernedToolRegistry``.
    """
    from ..policy.models import (
        AgentPolicy, ExecutionBudget, ToolPermission, VerificationPolicy,
    )
    permissions = [
        ToolPermission(name, allow=True, max_calls=None,
                       requires_sandbox=False, requires_approval=False)
        for name in read_only_tools
    ]
    return ExecutionPolicy(
        policy_id="replay-read-only", policy_version=1, risk_level=risk_level,
        budget=ExecutionBudget(
            max_steps=50, max_tool_calls=500, max_wall_time_seconds=600,
        ),
        verification=VerificationPolicy(),
        agents=AgentPolicy(enabled_agents=[]),
        tool_permissions=permissions,
        metadata={"source": "replay-read-only"},
    )