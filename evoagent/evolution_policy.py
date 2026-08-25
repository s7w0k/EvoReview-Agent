"""Evolution approval and deployment policy (closed-loop WP4)."""
from typing import Optional

APPROVAL_ALWAYS = "always"
APPROVAL_HIGH_RISK = "high-risk"
APPROVAL_NEVER = "never"

APPROVAL_POLICIES = frozenset({APPROVAL_ALWAYS, APPROVAL_HIGH_RISK, APPROVAL_NEVER})

RISK_HIGH = "high"


def requires_approval(
    policy: str,
    *,
    risk_level: str = "",
    cross_repo: bool = False,
    permissions_change: bool = False,
) -> bool:
    """Whether a candidate needs human approval before entering shadow."""
    if policy == APPROVAL_ALWAYS:
        return True
    if policy == APPROVAL_HIGH_RISK:
        return risk_level == RISK_HIGH or cross_repo or permissions_change
    if policy == APPROVAL_NEVER:
        return False
    raise ValueError("unsupported approval policy: %s" % policy)


def production_profile_enabled(production_profile: bool) -> bool:
    return bool(production_profile)


def default_policy(production_profile: bool) -> str:
    """Production defaults to ``always``; development keeps the same safe default."""
    return APPROVAL_ALWAYS


def emergency_rollback_allowed(
    policy: str,
    *,
    version_was_active: bool,
    target_is_historical: bool,
) -> bool:
    """Emergency restoration of a historical version bypasses the normal path.

    The caller must still pass the actor/permission gate in candidate_lifecycle.
    """
    return bool(version_was_active or target_is_historical)


def requires_second_approver(
    approver_id: str,
    creator_id: str,
    *,
    risk_level: str = "",
    dual_approval_enabled: bool = True,
) -> bool:
    """A high-risk candidate cannot be approved by its own creator.

    When dual approval is enabled, ``approver_id == creator_id`` on a high-risk
    change means a second, independent approver is required.
    """
    if not dual_approval_enabled:
        return False
    return risk_level == RISK_HIGH and bool(approver_id) and approver_id == creator_id
