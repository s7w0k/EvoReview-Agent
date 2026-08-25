"""Structured Hypothesis model for the closed loop (WP2).

A Hypothesis is the structured output of Reflection: it records *what* failed,
*why*, and *what change* is proposed, together with the evidence and the risk of
that change.  Hypotheses are data, never code: this module forbids emitting
arbitrary Python/Shell and never treats raw feedback text as an instruction.
"""
import uuid
from typing import Any, Dict, List, Optional

# --- change types allowed in the initial closed loop -----------------------
PROMPT_PATCH = "prompt_patch"
RULE_ADD = "rule_add"
RULE_TIGHTEN = "rule_tighten"
RULE_EXCEPTION = "rule_exception"
RULE_REMOVE = "rule_remove"
NO_CHANGE = "no_change"
PROCEDURE_PROPOSAL = "procedure_proposal"
TOOL_PROPOSAL = "tool_proposal"

CHANGE_TYPES = frozenset({
    PROMPT_PATCH, RULE_ADD, RULE_TIGHTEN, RULE_EXCEPTION, RULE_REMOVE,
    NO_CHANGE, PROCEDURE_PROPOSAL, TOOL_PROPOSAL,
})

# Only Prompt/Rule changes may auto-materialize; Procedure/Tool changes always
# remain human-review proposals.
AUTO_MATERIALIZABLE = frozenset({
    PROMPT_PATCH, RULE_ADD, RULE_TIGHTEN, RULE_EXCEPTION, RULE_REMOVE,
})
MANUAL_PROPOSAL = frozenset({PROCEDURE_PROPOSAL, TOOL_PROPOSAL})

# --- risk levels -----------------------------------------------------------
RISK_LOW = "low"
RISK_HIGH = "high"
RISK_LEVELS = frozenset({RISK_LOW, RISK_HIGH})

# --- lifecycle states ------------------------------------------------------
STATUS_DRAFT = "draft"
STATUS_REVIEWED = "reviewed"
STATUS_APPROVED = "approved"
STATUS_MATERIALIZED = "materialized"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUSES = frozenset({
    STATUS_DRAFT, STATUS_REVIEWED, STATUS_APPROVED, STATUS_MATERIALIZED,
    STATUS_REJECTED, STATUS_EXPIRED,
})

# Legal hypothesis transitions (mirrors the plan's 4.2 state machine).
TRANSITIONS = {
    STATUS_DRAFT: {STATUS_REVIEWED, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED},
    STATUS_REVIEWED: {STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED},
    STATUS_APPROVED: {STATUS_MATERIALIZED, STATUS_REJECTED, STATUS_EXPIRED},
    STATUS_MATERIALIZED: set(),
    STATUS_REJECTED: set(),
    STATUS_EXPIRED: set(),
}


def is_valid_change_type(change_type: str) -> bool:
    return change_type in CHANGE_TYPES


def is_auto_materializable(change_type: str) -> bool:
    return change_type in AUTO_MATERIALIZABLE


def is_manual_proposal(change_type: str) -> bool:
    return change_type in MANUAL_PROPOSAL


def is_valid_status(status: str) -> bool:
    return status in STATUSES


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def compute_risk(
    change_type: str,
    *,
    expands_scope: bool = False,
    lowers_severity: bool = False,
    affects_high_risk_rule: bool = False,
    requests_permissions: Optional[List[str]] = None,
    cross_repo: bool = False,
) -> str:
    """Deterministically classify a proposed change as low/high risk.

    Procedure/Tool proposals and ``no_change`` are special-cased: no_change is
    inert (low risk), while Procedure/Tool proposals always require human review
    and are therefore high risk from the loop's perspective.
    """
    if change_type == NO_CHANGE:
        return RISK_LOW
    if not is_auto_materializable(change_type):
        return RISK_HIGH
    if expands_scope or lowers_severity or affects_high_risk_rule or cross_repo:
        return RISK_HIGH
    if requests_permissions:
        return RISK_HIGH
    return RISK_LOW


def validate_hypothesis(hypothesis: Dict[str, Any]) -> List[str]:
    """Return a list of quality-gate violations (empty when the hypothesis is valid).

    The checks enforce the WP2 quality gates at the data layer: valid change
    type/status, non-empty diagnosis fields, explicit scope, declared
    permissions, at least one evidence id, and an objective evaluation
    expectation for auto-materializable changes.
    """
    errors: List[str] = []

    if not is_valid_change_type(hypothesis.get("change_type", "")):
        errors.append("invalid change_type")
    if not is_valid_status(hypothesis.get("status", "")):
        errors.append("invalid status")

    for field in ("problem_type", "failure_signature", "root_cause", "rationale"):
        if not str(hypothesis.get(field, "")).strip():
            errors.append("missing %s" % field)

    affected = hypothesis.get("affected_domains")
    if not isinstance(affected, list) or not affected:
        errors.append("affected_domains must be a non-empty list")

    permissions = hypothesis.get("permissions")
    if not isinstance(permissions, list):
        errors.append("permissions must be a list")

    evidence_ids = hypothesis.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        errors.append("evidence_ids must be a non-empty list")

    evaluation = hypothesis.get("evaluation_requirements")
    if not isinstance(evaluation, dict):
        errors.append("evaluation_requirements must be an object")
    elif is_auto_materializable(hypothesis.get("change_type", "")):
        if not evaluation.get("expected"):
            errors.append("auto-materializable change needs an objective expected effect")

    if not isinstance(hypothesis.get("expected_effect"), dict):
        errors.append("expected_effect must be an object")

    return errors


def new_hypothesis(
    *,
    tenant_id: str,
    problem_type: str,
    failure_signature: str,
    root_cause: str,
    change_type: str,
    job_id: str = "",
    repository_scope: Optional[str] = None,
    expected_effect: Optional[Dict[str, Any]] = None,
    affected_domains: Optional[List[str]] = None,
    risk_level: Optional[str] = None,
    permissions: Optional[List[str]] = None,
    evaluation_requirements: Optional[Dict[str, Any]] = None,
    rationale: str = "",
    evidence_ids: Optional[List[str]] = None,
    status: str = STATUS_DRAFT,
    reviewed_by: Optional[str] = None,
    source_case_ids: Optional[List] = None,
    source_task_ids: Optional[List] = None,
) -> Dict[str, Any]:
    """Build a fully-populated Hypothesis dict with deterministic defaults."""
    affected_domains = list(affected_domains or [])
    permissions = list(permissions or [])
    evidence_ids = list(evidence_ids or [])
    expected_effect = dict(expected_effect or {})
    evaluation_requirements = dict(evaluation_requirements or {})
    source_case_ids = list(source_case_ids or [])
    source_task_ids = list(source_task_ids or [])
    if risk_level is None:
        risk_level = compute_risk(
            change_type,
            requests_permissions=permissions,
            cross_repo=repository_scope is None,
        )
    return {
        "id": uuid.uuid4().hex,
        "job_id": job_id or "",
        "tenant_id": tenant_id,
        "repository_scope": repository_scope,
        "problem_type": problem_type,
        "failure_signature": failure_signature,
        "root_cause": root_cause,
        "change_type": change_type,
        "expected_effect": expected_effect,
        "affected_domains": affected_domains,
        "risk_level": risk_level,
        "permissions": permissions,
        "evaluation_requirements": evaluation_requirements,
        "rationale": rationale,
        "evidence_ids": evidence_ids,
        "status": status,
        "reviewed_by": reviewed_by,
        "provenance": {
            "source_experience_ids": list(evidence_ids),
            "source_case_ids": source_case_ids,
            "source_task_ids": source_task_ids,
        },
    }
