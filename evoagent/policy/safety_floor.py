"""Immutable safety floor applied last during policy resolution.

Every override layer (tenant / repository / task) may only tighten a policy;
the safety floor guarantees the posture can never be weakened below the floor
even when a later layer tries to relax it.  Enforcement runs *after* all other
layers so it wins regardless of precedence:

* high / critical risk levels keep a hardened ``VerificationPolicy``
* ``mandatory_tool_denies`` are re-applied as hard denies (a task allow cannot
  re-enable them)
* ``mandatory_approval_tools`` always require human approval
"""
from dataclasses import dataclass, field, replace
from typing import List, Optional, Set

from .models import ExecutionPolicy, RISK_RANK, ToolPermission, VerificationPolicy


@dataclass(frozen=True)
class SafetyFloor:
    """The system-wide, non-overridable minimum posture."""

    minimum_risk_level: str = "low"
    require_critic: bool = True
    require_evidence: bool = True
    require_verifier: bool = True
    require_sandbox: bool = True
    mandatory_tool_denies: Set[str] = field(default_factory=set)
    mandatory_approval_tools: Set[str] = field(default_factory=set)


# The default floor hardens only high / critical policies and denies nothing
# extra; products can supply a stricter floor per deployment.
SYSTEM_SAFETY_FLOOR = SafetyFloor(
    minimum_risk_level="low",
    require_critic=True,
    require_evidence=True,
    require_verifier=True,
    require_sandbox=True,
    mandatory_tool_denies=set(),
    mandatory_approval_tools=set(),
)


def apply_safety_floor(
    policy: ExecutionPolicy,
    floor: Optional[SafetyFloor] = None,
    task_config: Optional[dict] = None,
) -> ExecutionPolicy:
    """Re-assert the immutable floor over a fully-resolved policy."""
    floor = floor or SYSTEM_SAFETY_FLOOR

    # 1. Minimal risk level: a floor misconfiguration can never surface
    #    a relaxed policy for the resolved risk.
    resolved_floor = floor
    risk_level = policy.risk_level
    if RISK_RANK[risk_level] < RISK_RANK[floor.minimum_risk_level]:
        risk_level = floor.minimum_risk_level
        floor = replace(resolved_floor, minimum_risk_level=risk_level)

    # 2. Hardening applies to high / critical risk levels only.
    hardened = RISK_RANK[risk_level] >= RISK_RANK["high"]
    verification: VerificationPolicy = policy.verification
    if hardened:
        verification = replace(
            verification,
            critic_required=verification.critic_required or floor.require_critic,
            evidence_required=verification.evidence_required or floor.require_evidence,
            verifier_required=verification.verifier_required or floor.require_verifier,
            sandbox_required=verification.sandbox_required or floor.require_sandbox,
        )

    # 3. Re-assert mandatory denies & approvals on the merged permission list.
    permissions: List[ToolPermission] = list(policy.tool_permissions)
    by_name = {p.tool_name: p for p in permissions}
    for denied in floor.mandatory_tool_denies:
        existing = by_name.get(denied)
        by_name[denied] = ToolPermission(
            denied, allow=False, requires_sandbox=False, requires_approval=False,
        ) if existing is None else replace(existing, allow=False)
    for must_approve in floor.mandatory_approval_tools:
        existing = by_name.get(must_approve)
        by_name[must_approve] = ToolPermission(
            must_approve, allow=True, requires_approval=True,
        ) if existing is None else replace(existing, requires_approval=True)
    permissions = list(by_name.values())

    return replace(
        policy,
        risk_level=risk_level,
        verification=verification,
        tool_permissions=permissions,
        metadata=dict(policy.metadata, safety_floor_applied=True),
    )