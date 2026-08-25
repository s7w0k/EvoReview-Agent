"""Template-based runtime-policy candidate generation.

Per the plan (section 9.5) the first evolution stage must not let an LLM mutate
policies freely.  Instead, we derive candidates from a base ``ExecutionPolicy``
through a closed set of deterministic templates: add / remove an agent, raise /
lower step budget, or toggle evidence requirements.
"""
from dataclasses import dataclass, field
from dataclasses import replace
from enum import Enum
from typing import Any, Dict, List, Optional

from evoagent.policy.models import AgentPolicy, ExecutionPolicy

from .evolution_scope import assert_evolvable


class CandidateOperation(str, Enum):
    REMOVE_AGENT = "remove_agent"
    ADD_AGENT = "add_agent"
    LOWER_MAX_STEPS = "lower_max_steps"
    RAISE_MAX_STEPS = "raise_max_steps"
    ENABLE_EVIDENCE = "enable_evidence"
    DISABLE_EVIDENCE = "disable_evidence"


# The policy fields each operation touches (plan section 11.1 whitelist).
_OPERATION_FIELDS: Dict[CandidateOperation, tuple] = {
    CandidateOperation.REMOVE_AGENT: ("enabled_agents",),
    CandidateOperation.ADD_AGENT: ("enabled_agents",),
    CandidateOperation.LOWER_MAX_STEPS: ("max_steps",),
    CandidateOperation.RAISE_MAX_STEPS: ("max_steps",),
    CandidateOperation.ENABLE_EVIDENCE: ("evidence_required",),
    CandidateOperation.DISABLE_EVIDENCE: ("evidence_required",),
}


def candidate_signature(
    parent: ExecutionPolicy,
    operation: CandidateOperation,
    scope: str = "runtime",
    *,
    details: Optional[str] = None,
) -> str:
    """Deterministic signature for dedupe / cooldown / repeated-failure block.

    Computed from the parent version, the normalised mutation and the scope
    (plan section 11.3).  Identical parents + mutations + scope collide, so a
    repeated identical attempt can be recognised and blocked.
    """
    import hashlib

    fields = _OPERATION_FIELDS.get(operation, ())
    state = {field: _snapshot_field(parent, field) for field in fields}
    payload = ":".join([
        str(parent.policy_id),
        str(parent.policy_version),
        operation.value,
        scope,
        _stable_json(state),
        details or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class PolicyCandidate:
    """A policy derived from a parent through a named operation."""

    candidate_id: str
    policy: ExecutionPolicy
    parent_policy_id: str
    operation: CandidateOperation
    hypothesis_id: Optional[str] = None
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    signature: str = ""
    changed_fields: Dict[str, tuple] = field(default_factory=dict)
    scope: str = "runtime"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "parent_policy_id": self.parent_policy_id,
            "operation": self.operation.value,
            "hypothesis_id": self.hypothesis_id,
            "created_at": self.created_at,
            "signature": self.signature,
            "scope": self.scope,
            "changed_fields": {
                key: {"before": before, "after": after}
                for key, (before, after) in self.changed_fields.items()
            },
            "policy": self.policy.to_dict(),
            "metadata": dict(self.metadata),
        }


class PolicyCandidateGenerator:
    """Applies the closed template set over a parent policy."""

    def __init__(self, id_prefix: str = "pol-cand"):
        self._prefix = id_prefix

    # -- public API ---------------------------------------------------------

    def generate(
        self,
        parent: ExecutionPolicy,
        operations: Optional[List[CandidateOperation]] = None,
        *,
        add_agent: str = "",
        remove_agent: Optional[str] = None,
        step_delta: int = 2,
        hypothesis_id: Optional[str] = None,
    ) -> List[PolicyCandidate]:
        """Derive one candidate for each requested template operation."""
        if operations is None:
            operations = [
                CandidateOperation.REMOVE_AGENT,
                CandidateOperation.ADD_AGENT,
                CandidateOperation.LOWER_MAX_STEPS,
                CandidateOperation.RAISE_MAX_STEPS,
                CandidateOperation.ENABLE_EVIDENCE,
                CandidateOperation.DISABLE_EVIDENCE,
            ]

        candidates: List[PolicyCandidate] = []
        for index, operation in enumerate(operations):
            try:
                policy = self._apply(parent, operation, remove_agent=remove_agent,
                                     add_agent=add_agent, step_delta=step_delta)
            except (ValueError, TypeError):
                # An operation may be inapplicable to this parent; skip it
                # rather than emitting a broken candidate.
                continue
            # Every mutation must stay inside the evolvable-field whitelist.
            assert_evolvable(_OPERATION_FIELDS.get(operation, ()))
            changed = _collect_changes(parent, policy, operation)
            candidates.append(PolicyCandidate(
                candidate_id=f"{self._prefix}-{index + 1}",
                policy=policy,
                parent_policy_id=parent.policy_id,
                operation=operation,
                hypothesis_id=hypothesis_id,
                signature=candidate_signature(
                    parent, operation, details=";".join(
                        f"{k}={before}->{after}"
                        for k, (before, after) in changed.items())),
                changed_fields=changed,
            ))
        return candidates

    # -- templates ----------------------------------------------------------

    def _apply(self, parent, op: CandidateOperation, *, remove_agent, add_agent,
               step_delta) -> ExecutionPolicy:
        policy_id = f"{parent.policy_id}-{op.value}"
        version = parent.policy_version + 1

        agents = parent.agents
        base_kwargs: Dict[str, Any] = {
            "policy_id": policy_id,
            "policy_version": version,
            "risk_level": parent.risk_level,
            "budget": parent.budget,
            "retry": parent.retry,
            "agents": agents,
            "tool_permissions": parent.tool_permissions,
            "metadata": dict(parent.metadata),
        }

        if op is CandidateOperation.REMOVE_AGENT:
            target = remove_agent or (agents.enabled_agents[0]
                                      if agents.enabled_agents else "")
            if not target:
                raise ValueError("remove_agent requires a target agent")
            enabled = [name for name in agents.enabled_agents if name != target]
            base_kwargs["agents"] = replace(
                agents, enabled_agents=enabled)
            base_kwargs["metadata"]["evolved"] = {"removed_agent": target}

        elif op is CandidateOperation.ADD_AGENT:
            if not add_agent:
                raise ValueError("add_agent requires an agent name")
            enabled = list(agents.enabled_agents)
            if add_agent not in enabled:
                enabled = enabled + [add_agent]
            base_kwargs["agents"] = replace(agents, enabled_agents=enabled)
            base_kwargs["metadata"]["evolved"] = {"added_agent": add_agent}

        elif op is CandidateOperation.LOWER_MAX_STEPS:
            new_steps = max(1, parent.budget.max_steps - step_delta)
            base_kwargs["budget"] = replace(parent.budget, max_steps=new_steps)
            base_kwargs["metadata"]["evolved"] = {"max_steps": new_steps}

        elif op is CandidateOperation.RAISE_MAX_STEPS:
            new_steps = parent.budget.max_steps + step_delta
            base_kwargs["budget"] = replace(parent.budget, max_steps=new_steps)
            base_kwargs["metadata"]["evolved"] = {"max_steps": new_steps}

        elif op is CandidateOperation.ENABLE_EVIDENCE:
            base_kwargs["verification"] = replace(
                parent.verification, evidence_required=True)
            base_kwargs["metadata"]["evolved"] = {"evidence_required": True}

        elif op is CandidateOperation.DISABLE_EVIDENCE:
            base_kwargs["verification"] = replace(
                parent.verification, evidence_required=False)
            base_kwargs["metadata"]["evolved"] = {"evidence_required": False}

        else:
            raise ValueError(f"unknown candidate operation: {op}")

        return ExecutionPolicy(**base_kwargs)


def _collect_changes(
    parent: ExecutionPolicy,
    policy: ExecutionPolicy,
    operation: CandidateOperation,
) -> Dict[str, tuple]:
    """Return ``{field: (before, after)}`` for the fields an op may touch."""
    changes: Dict[str, tuple] = {}
    for field in _OPERATION_FIELDS.get(operation, ()):
        before = _snapshot_field(parent, field)
        after = _snapshot_field(policy, field)
        if before != after:
            changes[field] = (before, after)
    return changes


def _snapshot_field(policy: ExecutionPolicy, field: str):
    if field == "enabled_agents":
        return list(policy.agents.enabled_agents)
    if field == "max_parallel_agents":
        return policy.agents.max_parallel_agents
    if field == "max_steps":
        return policy.budget.max_steps
    if field == "max_tool_calls":
        return policy.budget.max_tool_calls
    if field == "critic_required":
        return policy.verification.critic_required
    if field == "evidence_required":
        return policy.verification.evidence_required
    if field == "verifier_required":
        return policy.verification.verifier_required
    if field == "max_retries":
        return policy.retry.max_retries
    if field == "read_only_tool_allowlist":
        return sorted(tp.tool_name for tp in policy.tool_permissions if tp.allow)
    if field == "retry_count":
        return policy.retry.max_retries
    return getattr(policy, field, None)


def _stable_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)