"""Deterministic clustering of duplicate review findings.

Findings on the same ``(path, line)`` reported with different rule ids are
grouped into one cluster; the primary finding keeps the highest severity (then
highest confidence).  The clustering switch defaults to off; ``shadow`` only
records statistics without changing the output, ``on`` merges the cluster into
its primary finding.
"""
from typing import Dict, List, Tuple

from .models import Finding

_SEVERITY_RANK = {
    "critical": 4, "high": 3, "medium": 2, "low": 1,
}


def _primary(group: List[Finding]) -> Finding:
    return max(
        group,
        key=lambda item: (
            _SEVERITY_RANK.get(item.severity.value, 0),
            item.confidence,
        ),
    )


def cluster_findings(
    findings: List[Finding], mode: str = "off",
) -> Tuple[List[Finding], Dict[str, object]]:
    """Cluster findings by (path, line).

    Returns ``(result_findings, metadata)`` where metadata always carries the
    mode, cluster count and duplicate count.  Only ``mode == "on"`` changes the
    returned findings; ``shadow`` and ``off`` return the input unchanged.
    """
    mode = (mode or "off").strip().lower()
    if not findings:
        metadata = {"clustering": mode, "clusters": 0, "duplicates": 0}
        return list(findings), metadata

    groups: Dict[Tuple[str, int], List[Finding]] = {}
    order: List[Tuple[str, int]] = []
    for finding in findings:
        key = (finding.path, finding.line)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(finding)

    metadata = {
        "clustering": mode,
        "clusters": len(order),
        "duplicates": len(findings) - len(order),
    }
    if mode != "on":
        return list(findings), metadata
    return [_primary(groups[key]) for key in order], metadata
