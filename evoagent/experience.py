"""Deterministic experience routing, evidence normalization and fingerprinting.

Work Package 3: a bypass store that observes feedback without changing the
existing ``failure_cases`` / memory path.  Routing is fully deterministic (no
LLM) so the same feedback always maps to the same experience type.
"""
import hashlib
import re
import unicodedata
from typing import Any, Dict, List, Optional

# Experience lifecycle states.
OBSERVED = "observed"
CORROBORATED = "corroborated"
CONSUMED = "consumed"
REJECTED = "rejected"

# Experience scope (WP7).  Raw experience is never auto-shared across tenants;
# repository-local is the safe default, tenant-shared requires independent
# confirmation across repositories, global-builtin is maintainer-only.
SCOPE_REPOSITORY_LOCAL = "repository-local"
SCOPE_TENANT_SHARED = "tenant-shared"
SCOPE_GLOBAL_BUILTIN = "global-builtin"
SCOPES = (SCOPE_REPOSITORY_LOCAL, SCOPE_TENANT_SHARED, SCOPE_GLOBAL_BUILTIN)

# Experience types (routing result).
RULE_CANDIDATE = "rule_candidate"
SEMANTIC_MEMORY = "semantic_memory"
RULE_REFINEMENT = "rule_refinement"
REPAIR_CANDIDATE = "repair_candidate"
POSITIVE_SIGNAL = "positive_signal"

# Feedback categories accepted by record_feedback.
MISSED_ISSUE = "missed_issue"
FALSE_POSITIVE = "false_positive"
BAD_FIX = "bad_fix"
ACCEPTED = "accepted"

MAX_EVIDENCE = 240
RULE_ID = re.compile(r"[A-Z][A-Z0-9_-]{1,79}")

# Secret masking: redact values assigned to secret-looking keys and known formats.
_SECRET_ASSIGN = re.compile(
    r"(\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|auth[_-]?token|"
    r"private[_-]?key)\b\s*[=:]\s*)['\"]?[A-Za-z0-9_\-./+]{4,}",
    re.IGNORECASE,
)
_AWS_ACCESS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


def mask_secrets(value: Optional[str]) -> str:
    """Mask known secret formats (key=value assignments, AWS access keys).

    Used to redact sensitive text before persistence / display while keeping
    everything else intact.  Applies no truncation.
    """
    text = unicodedata.normalize("NFKC", value or "")
    text = _SECRET_ASSIGN.sub(r"\1<REDACTED>", text)
    text = _AWS_ACCESS_KEY.sub("<REDACTED>", text)
    return text


def normalize_evidence(value: Optional[str]) -> str:
    """Normalize evidence for stable fingerprints.

    Only Unicode/whitespace normalization, length bounding and known-secret
    masking are applied.  Variable names, strings and paths are NOT generalized
    so distinct defects are not merged.
    """
    text = mask_secrets(value)
    text = " ".join(text.split())
    return text[:MAX_EVIDENCE]


def _is_complete_rule(finding: Dict[str, Any]) -> bool:
    rule_id = str(finding.get("rule_id", "")).strip()
    path = str(finding.get("path", "")).strip()
    try:
        line = int(finding.get("line", 0))
    except (TypeError, ValueError):
        line = 0
    return bool(RULE_ID.fullmatch(rule_id) and path and line > 0)


def extract_evidence(finding: Dict[str, Any], added_lines: list) -> Optional[str]:
    """Return the normalized content of the added line the finding points to."""
    path = str(finding.get("path", ""))
    try:
        line = int(finding.get("line", 0))
    except (TypeError, ValueError):
        return None
    for changed in added_lines or []:
        if getattr(changed, "path", None) == path and getattr(changed, "line", None) == line:
            return normalize_evidence(getattr(changed, "content", ""))
    return None


def route(
    category: str, finding: Optional[Dict[str, Any]], added_lines: list,
) -> Optional[Dict[str, Any]]:
    """Map a feedback record to a deterministic experience type.

    Returns None when the feedback cannot produce a useful experience.
    """
    finding = dict(finding or {})
    if category == MISSED_ISSUE:
        if _is_complete_rule(finding):
            evidence = extract_evidence(finding, added_lines)
            if evidence:
                return {
                    "experience_type": RULE_CANDIDATE,
                    "confidence": 0.85,
                    "rule_id": str(finding.get("rule_id", "")).strip(),
                    "evidence": evidence,
                }
        # Incomplete missed issue (no valid rule/path/line or no added-line
        # evidence) is unsuitable to become a rule candidate.
        return {"experience_type": SEMANTIC_MEMORY, "confidence": 0.5, "rule_id": "", "evidence": None}
    if category == FALSE_POSITIVE:
        rule_id = str(finding.get("rule_id", "")).strip()
        return {
            "experience_type": RULE_REFINEMENT, "confidence": 0.7,
            "rule_id": rule_id if RULE_ID.fullmatch(rule_id) else "",
            "evidence": None,
        }
    if category == BAD_FIX:
        return {"experience_type": REPAIR_CANDIDATE, "confidence": 0.7, "rule_id": "", "evidence": None}
    if category == ACCEPTED:
        return {"experience_type": POSITIVE_SIGNAL, "confidence": 0.9, "rule_id": "", "evidence": None}
    return None


def fingerprint(
    tenant_id: str, repository: Optional[str], experience_type: str,
    rule_id: str, evidence: str,
) -> str:
    """Stable aggregate fingerprint. task_id is deliberately excluded so the same
    evidence across tasks corroborates; the DB unique key keeps per-task dedup."""
    raw = "|".join((tenant_id, repository or "", experience_type, rule_id, normalize_evidence(evidence)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_experience(
    tenant_id: str, repository: Optional[str], task_id: str,
    category: str, finding: Optional[Dict[str, Any]], added_lines: list,
) -> Optional[Dict[str, Any]]:
    """Route one feedback record into a ready-to-persist experience (or None)."""
    routed = route(category, finding, added_lines)
    if routed is None:
        return None
    evidence = routed["evidence"]
    fp = fingerprint(
        tenant_id, repository, routed["experience_type"], routed["rule_id"], evidence or "",
    )
    return {
        "tenant_id": tenant_id, "repository": repository, "task_id": task_id,
        "source_type": "feedback", "category": category,
        "experience_type": routed["experience_type"],
        "fingerprint": fp,
        "payload": {"finding": finding},
        "evidence": evidence,
        "confidence": routed["confidence"],
        "status": OBSERVED,
        "scope": SCOPE_REPOSITORY_LOCAL,
    }