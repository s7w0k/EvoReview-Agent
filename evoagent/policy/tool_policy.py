"""Tool governance: metadata, authorization and side-effect protection.

Harness does not just know which tools exist; it decides which agent, in which
task, under which risk level and budget, may invoke which tool.  The
``ToolPolicyEngine`` sits between an agent's tool request and the actual call.
"""
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from .models import ExecutionPolicy, RISK_RANK, ToolPermission


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    risk_level: str = "low"
    side_effect: bool = False
    idempotent: bool = True
    retryable: bool = True
    requires_sandbox: bool = False
    requires_approval: bool = False
    timeout_seconds: float = 30.0
    allowed_agents: List[str] = field(default_factory=list)
    tenant_scoped: bool = False
    compensatable: bool = False
    compensation_tool: Optional[str] = None
    # True when the handler must run in an isolated subprocess so a blocking
    # call can be terminated when the timeout fires.  ``command`` is the shell
    # template executed when the tool is e.g. ``run_tests``.
    blocking: bool = False
    command: Optional[str] = None


@dataclass
class ToolDecision:
    allowed: bool
    reason: Optional[str] = None
    requires_sandbox: bool = False
    requires_approval: bool = False


class ToolPermissionDenied(RuntimeError):
    """A tool call was rejected by the tool policy engine."""


class ToolBudgetExceeded(RuntimeError):
    """The execution policy tool-call budget was exhausted."""


class ToolPolicyEngine:
    """Authorize tool calls against policy and tool metadata."""

    def __init__(self, metadata: Dict[str, ToolMetadata]):
        self.metadata = dict(metadata)

    def register(self, metadata: ToolMetadata) -> None:
        self.metadata[metadata.name] = metadata

    def authorize(
        self,
        agent_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        execution_policy: ExecutionPolicy,
        runtime_state: Dict[str, Any],
    ) -> ToolDecision:
        meta = self.metadata.get(tool_name)
        if meta is None:
            return ToolDecision(False, reason="unknown tool: %s" % tool_name)
        # Policy allow-list.
        permission = execution_policy.tool_permission(tool_name)
        if permission is not None and not permission.allow:
            return ToolDecision(False, reason="tool disallowed by execution policy")
        if permission is None and not self._default_allowed(meta):
            return ToolDecision(False, reason="tool not in policy allow-list")
        # Risk: a high-risk tool is forbidden under a low/medium policy.
        if RISK_RANK[meta.risk_level] > RISK_RANK[execution_policy.risk_level]:
            return ToolDecision(
                False,
                reason="tool risk (%s) exceeds policy risk (%s)"
                % (meta.risk_level, execution_policy.risk_level),
            )
        # Agent restriction.
        if meta.allowed_agents and agent_id not in meta.allowed_agents:
            return ToolDecision(
                False, reason="agent %s not allowed to call %s" % (agent_id, tool_name)
            )
        # Budget.
        calls = (runtime_state.get("tool_call_counts") or {}).get(tool_name, 0)
        if permission and permission.max_calls is not None and calls >= permission.max_calls:
            return ToolDecision(
                False, reason="tool %s exceeds max_calls budget" % tool_name
            )
        meta_calls = (runtime_state.get("tool_call_counts") or {}).get("__all__", 0)
        if meta_calls >= execution_policy.budget.max_tool_calls:
            return ToolDecision(
                False, reason="total tool-call budget exceeded"
            )
        # Side-effect protection: non-idempotent tools require approval.
        requires_approval = meta.requires_approval or (meta.side_effect and not meta.idempotent)
        sandbox = meta.requires_sandbox or (
            permission.requires_sandbox if permission else False
        )
        approval = meta.requires_approval or (permission.requires_approval if permission else False)
        return ToolDecision(
            allowed=True,
            requires_sandbox=sandbox,
            requires_approval=requires_approval or approval,
        )

    @staticmethod
    def _default_allowed(meta: ToolMetadata) -> bool:
        return meta.risk_level == "low" and not meta.side_effect


def merge_tool_permissions(
    base: List[ToolPermission], override: List[ToolPermission],
) -> List[ToolPermission]:
    """Merge two permission lists keyed by ``tool_name``.

    Each override entry replaces only the fields it carries for the same tool,
    keeping every remaining field from ``base``.  This is a *true merge* instead
    of ``base + override``: a more-specific layer (repository deny) overrides a
    lower-precedence layer (system allow) per tool, while tools only present in
    ``base`` stay untouched.  Immutable hard denies are re-applied afterwards by
    the safety floor so a task allow cannot re-enable them.
    """
    merged: Dict[str, ToolPermission] = {p.tool_name: p for p in base}
    for override_perm in override:
        existing = merged.get(override_perm.tool_name)
        if existing is None:
            merged[override_perm.tool_name] = override_perm
            continue
        merged[override_perm.tool_name] = replace(
            existing,
            allow=override_perm.allow,
            max_calls=override_perm.max_calls,
            requires_sandbox=override_perm.requires_sandbox,
            requires_approval=override_perm.requires_approval,
        )
    return list(merged.values())