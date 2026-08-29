"""Canonical finding identity for cross-scanner de-duplication (plan Phase 5).

Deterministic rule scanners and semantic (AST) analyzers can both report the
same underlying issue at the same changed line under different rule ids (e.g.
``SEC-EVAL`` from rule scanning and ``SEM-TAINTED-EXEC`` from the AST analyzer).
``canonical_identity`` maps each finding onto a shared
``(issue_family, path, line)`` key so those duplicates collapse into a single
finding without ever merging distinct vulnerabilities that merely share a line.
"""
from typing import Dict, List, Tuple

from .models import Finding

# Rule ids that describe one and the same underlying issue across scanners.
# The tuple's first member is the stable family key used when merging.
FAMILIES: Tuple[Tuple[str, ...], ...] = (
    ("SEC-EVAL", "SEM-TAINTED-EXEC"),
    ("SEC-SUBPROCESS-SHELL", "SEM-TAINTED-SUBPROCESS", "SEM-SHELL-INJECTION"),
)

_RULE_TO_FAMILY: Dict[str, str] = {}
for _group in FAMILIES:
    for _rule in _group:
        _RULE_TO_FAMILY.setdefault(_rule, _group[0])

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def canonical_family(rule_id: str) -> str:
    """Return a shared family id for cross-scanner duplicate rules.

    Rules that describe the same underlying issue collapse onto one family key;
    unrelated rules keep their own rule id so different vulnerabilities located
    on the same line are never merged.
    """
    return _RULE_TO_FAMILY.get(rule_id, rule_id)


def canonical_identity(finding: Finding) -> Tuple[str, str, int]:
    """Identity bucket deciding whether two findings are the same issue."""
    return (canonical_family(finding.rule_id), finding.path, int(finding.line))


def _sev(finding: Finding) -> int:
    return _SEVERITY_RANK.get(finding.severity.value, 0)


def merge_cross_scanner(findings: List[Finding]) -> List[Finding]:
    """Collapse findings that share a canonical identity.

    Keeps the strongest primary (highest severity, then highest confidence)
    among cross-scanner duplicates while preserving independent vulnerabilities
    that only share a source line.
    """
    merged: Dict[Tuple[str, str, int], Finding] = {}
    order: List[Tuple[str, str, int]] = []
    for finding in findings:
        key = canonical_identity(finding)
        current = merged.get(key)
        if current is None:
            merged[key] = finding
            order.append(key)
            continue
        if (_sev(finding), finding.confidence) > (_sev(current), current.confidence):
            merged[key] = finding
    return [merged[key] for key in order]


deduplicate_by_canonical_identity = merge_cross_scanner

__all__ = [
    "FAMILIES",
    "canonical_family",
    "canonical_identity",
    "merge_cross_scanner",
    "deduplicate_by_canonical_identity",
]