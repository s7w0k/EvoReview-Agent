"""Evolvable-field whitelist for candidate mutation (plan section 11.1).

Only a closed set of runtime-tuning knobs may be mutated by self-evolution.
Anything touching tenant isolation, auth, repo isolation, side-effect approval,
the critical-risk safety floor, secrets or the sandbox network boundary is
*forbidden* regardless of the measured utility gain.
"""
from typing import Any, Dict, Iterable, List

# Runtime-tuning fields that evolution may touch.
EVOLVABLE_FIELDS: set = {
    "enabled_agents",
    "max_parallel_agents",
    "max_steps",
    "max_tool_calls",
    "critic_required",
    "evidence_required",
    "verifier_required",
    "read_only_tool_allowlist",
    "retry_count",
    "max_retries",
}

# Hard safety fields that evolution must never mutate.
FORBIDDEN_FIELDS: set = {
    "tenant_isolation",
    "auth_required",
    "repo_isolation",
    "mandatory_side_effect_approval",
    "critical_risk_safety_floor",
    "secret_handling",
    "sandbox_network_boundary",
}


class ForbiddenEvolutionField(Exception):
    """Raised when an attempt is made to mutate a forbidden policy field."""


def validate_mutated_fields(fields: Iterable[str]) -> List[str]:
    """Return the forbidden fields among ``fields`` (empty when all safe).

    Raises nothing itself; the audit-list is returned so callers can record an
    explicit violation.
    """
    touched = {str(field) for field in fields} & FORBIDDEN_FIELDS
    return sorted(touched)


def assert_evolvable(fields: Iterable[str]) -> None:
    """Raise ``ForbiddenEvolutionField`` if any field is not evolvable.

    An unknown field is treated as banned -- evolution may only touch an
    explicitly whitelisted knob (fail-closed, per plan section 11.1).
    """
    banned = [str(field) for field in fields
              if str(field) not in EVOLVABLE_FIELDS]
    if banned:
        raise ForbiddenEvolutionField(
            "field(s) not on the evolvable whitelist: %s" % ", ".join(sorted(banned)))


def audit_mutation(fields: Iterable[str]) -> Dict[str, Any]:
    """Produce a safety audit record for a mutation's touched fields."""
    return {
        "touched_fields": sorted(str(field) for field in fields),
        "forbidden": validate_mutated_fields(fields),
        "allowed": sorted(
            str(field) for field in fields if str(field) in EVOLVABLE_FIELDS),
        "unknown_banned": sorted(
            str(field) for field in fields
            if str(field) not in EVOLVABLE_FIELDS and str(field) not in FORBIDDEN_FIELDS),
    }