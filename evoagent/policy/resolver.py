"""Resolve an ``ExecutionPolicy`` for a task.

The resolver applies a fixed priority chain so a more specific source never
silently overrides a stricter one:

    system default -> tenant override -> repository override -> risk profile -> task override

Risk never *lowers* the safety posture: it may only raise the effective policy
to a stricter level.  Override layers are provided as plain dicts keyed the same
way ``ExecutionPolicy.to_dict()`` serializes.
"""
from dataclasses import asdict, replace
from typing import Any, Dict, Optional

from .defaults import default_policy
from .models import ExecutionPolicy, RISK_RANK, ToolPermission
from .risk import RiskProfile
from .safety_floor import SafetyFloor, apply_safety_floor
from .tool_policy import merge_tool_permissions


class PolicyResolver:
    """Layer runtime / tenant / repository / task overrides over a base policy."""

    def __init__(self, defaults: Optional[Dict[str, ExecutionPolicy]] = None,
                 safety_floor: Optional[SafetyFloor] = None):
        self.defaults = defaults or {}
        self.safety_floor = safety_floor

    def resolve(
        self,
        task: Dict[str, Any],
        risk_profile: Optional[RiskProfile] = None,
        tenant_config: Optional[Dict[str, Any]] = None,
        repository_config: Optional[Dict[str, Any]] = None,
    ) -> ExecutionPolicy:
        risk = risk_profile or RiskProfile(level="low", score=0.0, reasons=["no profile"])
        level = risk.level

        # 1. System default for the risk level (same-level baseline).
        policy = self.defaults.get(level) or default_policy(level)

        # 2. Tenant override.
        if tenant_config:
            policy = self._apply_override(policy, tenant_config, "tenant")

        # 3. Repository override.
        if repository_config:
            policy = self._apply_override(policy, repository_config, "repository")

        # 4. Risk escalation: the effective risk may never drop below profile risk.
        base = default_policy(level)
        if RISK_RANK[base.risk_level] > RISK_RANK[policy.risk_level]:
            policy = replace(policy, risk_level=base.risk_level,
                             budget=base.budget, retry=base.retry,
                             verification=base.verification, agents=base.agents,
                             tool_permissions=list(base.tool_permissions),
                             metadata=dict(policy.metadata, risk_escalated=True))

        # 5. Task override (highest precedence, but cannot weaken immutable safety).
        task_config = task.get("policy") or task.get("execution_policy")
        if isinstance(task_config, dict):
            policy = self._apply_override(
                policy, task_config, "task",
                metadata={"task_override": True, "policy_id": policy.policy_id},
            )

        # 6. Immutable safety floor runs last: it can only raise the posture.
        if self.safety_floor is not None:
            policy = apply_safety_floor(policy, self.safety_floor)

        return policy

    @staticmethod
    def _apply_override(
        policy: ExecutionPolicy, override: Dict[str, Any], source: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionPolicy:
        current = policy.to_dict()
        # Only keys the caller actually provides may change; everything else keeps
        # the lower-precedence value.
        if "budget" in override:
            current["budget"].update(override["budget"])
        if "retry" in override:
            current["retry"].update(override["retry"])
        if "verification" in override:
            current["verification"].update(override.get("verification", {}))
        if "agents" in override:
            current["agents"].update(override["agents"])
        if "risk_level" in override:
            current["risk_level"] = override["risk_level"]
        if "tool_permissions" in override:
            base_perms = [ToolPermission(**p) for p in current["tool_permissions"]]
            override_perms = [ToolPermission(**p) for p in override["tool_permissions"]]
            merged = merge_tool_permissions(base_perms, override_perms)
            current["tool_permissions"] = [asdict(p) for p in merged]
        merged = ExecutionPolicy.from_dict(current)
        explicit = override.get("metadata") or {}
        return replace(
            merged,
            metadata=dict(merged.metadata, override_source=source, **explicit, **(metadata or {})),
        )

    def enforce_safety_floor(
        self,
        policy: ExecutionPolicy,
        risk_profile: Optional[RiskProfile] = None,
    ) -> ExecutionPolicy:
        """Guarantee a policy never drops below the immutable safety floor.

        A thin, explicit entry point used after deployment routing (section 4.3)
        so the *routed* policy (baseline or candidate) is still tightened if the
        system safety floor or the task's risk level demands it.
        """
        if self.safety_floor is not None:
            policy = apply_safety_floor(policy, self.safety_floor)
        if risk_profile is not None:
            floor = default_policy(risk_profile.level)
            if RISK_RANK[floor.risk_level] > RISK_RANK[policy.risk_level]:
                policy = replace(
                    policy, risk_level=floor.risk_level,
                    budget=floor.budget, retry=floor.retry,
                    verification=floor.verification, agents=floor.agents,
                    tool_permissions=list(floor.tool_permissions),
                    metadata=dict(policy.metadata, risk_enforced_after_routing=True))
        return policy

    @staticmethod
    def validate(policy: ExecutionPolicy) -> None:
        """Fail fast when a resolved policy is internally inconsistent."""
        if policy.budget.max_steps < 1 or policy.budget.max_tool_calls < 1:
            raise ValueError("policy budget must allow at least one step and tool call")
        if any(permission.allow for permission in policy.tool_permissions) is False and \
                not policy.tool_permissions:
            raise ValueError("policy must allow at least one tool")
        if policy.verification.sandbox_required and not any(
            p.requires_sandbox or p.allow for p in policy.tool_permissions
        ):
            raise ValueError("policy requires sandbox but no sandboxed tool is permitted")