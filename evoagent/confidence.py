"""Confidence enhancement and result classification for review findings.

Confidence combines the original model/rule confidence with:
- multi-agent consensus (how many specialists reported the same finding);
- historical false-positive rate for the rule, aggregated from feedback.

The ``EVOAGENT_CONFIDENCE_ENHANCE`` switch defaults to off, so legacy reports
keep their original confidence values unchanged.
"""
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from .agents import finding_key
from .models import Finding

DEFAULT_BUCKETS: Tuple[float, float] = (0.8, 0.5)


def parse_buckets(value: str) -> Tuple[float, float]:
    """Parse "confirmed_min,needs_review_min" into a descending pair in [0,1]."""
    try:
        parts = [float(item.strip()) for item in str(value).split(",")]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "EVOAGENT_CONFIDENCE_BUCKETS must be two comma-separated values in [0,1]"
        ) from exc
    if (
        len(parts) != 2
        or not all(0.0 <= item <= 1.0 for item in parts)
        or parts[0] < parts[1]
    ):
        raise ValueError(
            "EVOAGENT_CONFIDENCE_BUCKETS must be two descending values in [0,1]"
        )
    return (parts[0], parts[1])


def enhance_confidence(
    value: float, consensus: float, false_positive_rate: float, enabled: bool,
) -> float:
    """Deterministic confidence adjustment; unchanged when disabled."""
    if not enabled:
        return value
    consensus = max(0.0, min(1.0, consensus))
    consensus_factor = 0.7 + 0.3 * consensus
    fp_factor = max(0.1, 1.0 - max(0.0, min(1.0, false_positive_rate)))
    result = value * consensus_factor * fp_factor
    return max(0.05, min(1.0, round(result, 3)))


def consensus_of(key: str, sources: Dict[str, List[str]], total_agents: int) -> float:
    """Fraction of specialists that reported the finding with this key."""
    if total_agents <= 0:
        return 1.0
    supporters = len(sources.get(key, []) or [])
    return supporters / total_agents


def rule_fp_stats(cases: List[dict]) -> Dict[str, Dict[str, int]]:
    """Aggregate failure-case feedback per rule_id (exact finding signature)."""
    stats: Dict[str, Dict[str, int]] = {}
    for case in cases:
        finding = ((case.get("payload") or {}).get("finding") or {})
        rule_id = str(finding.get("rule_id", "")).strip().upper()
        if not rule_id:
            continue
        item = stats.setdefault(rule_id, {"total": 0, "false_positive": 0})
        item["total"] += 1
        if case.get("category") == "false_positive":
            item["false_positive"] += 1
    return stats


def fp_rate_of(rule_id: str, stats: Dict[str, Dict[str, int]]) -> float:
    item = stats.get(str(rule_id).strip().upper())
    if not item or item["total"] <= 0:
        return 0.0
    return item["false_positive"] / item["total"]


def apply_enhancement(
    findings: List[Finding], sources: Dict[str, List[str]], total_agents: int,
    cases: List[dict], enabled: bool,
) -> List[Finding]:
    """Return findings with enhanced confidence (unchanged when disabled)."""
    if not enabled or not findings:
        return findings
    stats = rule_fp_stats(cases)
    enhanced: List[Finding] = []
    for finding in findings:
        consensus = consensus_of(finding_key(finding), sources, total_agents)
        rate = fp_rate_of(finding.rule_id, stats)
        enhanced.append(replace(
            finding,
            confidence=enhance_confidence(
                finding.confidence, consensus, rate, True,
            ),
        ))
    return enhanced


def classify(
    findings: List[Finding], buckets: Tuple[float, float] = DEFAULT_BUCKETS,
) -> Dict[str, List[Dict[str, Any]]]:
    """Bucket findings into confirmed / needs_review / suggestion."""
    confirmed_min, needs_review_min = buckets
    result: Dict[str, List[Dict[str, Any]]] = {
        "confirmed": [], "needs_review": [], "suggestion": [],
    }
    for finding in findings:
        entry = {
            "rule_id": finding.rule_id,
            "path": finding.path,
            "line": finding.line,
            "confidence": finding.confidence,
        }
        if finding.confidence >= confirmed_min:
            result["confirmed"].append(entry)
        elif finding.confidence >= needs_review_min:
            result["needs_review"].append(entry)
        else:
            result["suggestion"].append(entry)
    return result
