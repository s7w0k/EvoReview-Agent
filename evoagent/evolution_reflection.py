"""Structured Reflection: Experience clustering -> Hypothesis (closed-loop WP2).

Reflection is fully deterministic (no LLM): it groups corroborated Experience /
reflection-event signals along the plan's dimensions, then emits a structured
:class:`~evoagent.hypothesis` change proposal.  Raw feedback text is treated as
*untrusted evidence* and is never copied into the generated ``root_cause``,
``rationale`` or ``expected_effect``; a prompt-injection-looking signal is
redacted before it can influence clustering or candidate content.
"""
import os
import re
from typing import Any, Dict, List, Optional

from . import experience as exp
from . import hypothesis as hyp

# Non-feedback reflection inputs (reflection inputs beyond the feedback table).
AGENT_FAILURE = "agent_failure"
TOOL_FAILURE = "tool_failure"
CRITIC_REJECTION = "critic_rejection"
REPAIR_GATE_FAILURE = "repair_gate_failure"
QUALITY_DRIFT = "quality_drift"

EVENT_KINDS = frozenset({
    AGENT_FAILURE, TOOL_FAILURE, CRITIC_REJECTION, REPAIR_GATE_FAILURE, QUALITY_DRIFT,
})

# Prompt-injection markers: feedback text matching these is dropped as evidence
# so it can never leak into a generated Hypothesis.
_INSTRUCTION_RE = re.compile(
    r"\b(ignore|disregard|forget|override|bypass|system\s*:|you\s+must|"
    r"you\s+are|act\s+as|do\s+not|never\s+check)\b",
    re.IGNORECASE,
)

_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".go": "go", ".java": "java", ".rb": "ruby", ".php": "php",
    ".c": "c", ".cpp": "cpp", ".cs": "csharp", ".rs": "rust",
}


def sanitize_evidence(value: Optional[str]) -> str:
    """Return evidence safe for clustering; injection-like text is redacted."""
    text = exp.mask_secrets(value)
    if _INSTRUCTION_RE.search(text):
        return "<untrusted-evidence-omitted>"
    return exp.normalize_evidence(text)


def _language(path: Optional[str]) -> str:
    ext = os.path.splitext(str(path or ""))[1].lower()
    return _EXT_LANG.get(ext, "unknown")


def _path_type(path: Optional[str]) -> str:
    p = str(path or "").lower()
    if p.endswith((".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf")):
        return "config"
    if p.endswith((".py", ".js", ".ts", ".go", ".java", ".rb", ".php", ".c", ".cpp", ".cs", ".rs")):
        return "source"
    return "other"


def _domain(problem_type: Optional[str]) -> str:
    p = (problem_type or "").upper()
    if p.startswith("SEC"):
        return "security"
    if p.startswith("PERF"):
        return "performance"
    if p.startswith("BUG") or p.startswith("CORRECT"):
        return "correctness"
    if p.startswith("STYLE"):
        return "style"
    return "general"


def _is_high_risk_rule(problem_type: Optional[str]) -> bool:
    return bool(problem_type) and (problem_type or "").upper().startswith("SEC")


def _signals_from_experiences(experiences) -> List[Dict[str, Any]]:
    signals = []
    for item in experiences or []:
        finding = (item.get("payload") or {}).get("finding") or {}
        signals.append({
            "id": item.get("id", ""),
            "tenant_id": item.get("tenant_id", "default"),
            "repository": item.get("repository"),
            "task_id": item.get("task_id", ""),
            "category": item.get("category", ""),
            "experience_type": item.get("experience_type", ""),
            "problem_type": str(finding.get("rule_id", "") or "").strip().upper(),
            "evidence": item.get("evidence") or "",
            "finding": finding,
            "confidence": item.get("confidence", 0.0),
            "path": str(finding.get("path", "") or ""),
        })
    return signals


def _signals_from_events(events) -> List[Dict[str, Any]]:
    signals = []
    for item in events or []:
        finding = item.get("finding") or {}
        category = item.get("event_type") or item.get("category") or ""
        signals.append({
            "id": item.get("id", ""),
            "tenant_id": item.get("tenant_id", "default"),
            "repository": item.get("repository"),
            "task_id": item.get("task_id", ""),
            "category": category,
            "experience_type": category,
            "problem_type": str(item.get("problem_type", "") or "").strip().upper(),
            "evidence": item.get("evidence") or "",
            "finding": finding,
            "confidence": item.get("confidence", 0.5),
            "path": str(finding.get("path", "") or ""),
        })
    return signals


def _cluster_key(signal: Dict[str, Any]):
    return (
        signal.get("tenant_id", "default"),
        signal.get("repository") or "",
        str(signal.get("problem_type", "") or "").strip().upper(),
        sanitize_evidence(signal.get("evidence") or ""),
        signal.get("category") or signal.get("experience_type") or "",
        _language(signal.get("path")),
        _path_type(signal.get("path")),
    )


def _summarize_cluster(key, members) -> Dict[str, Any]:
    repos = {m.get("repository") for m in members if m.get("repository")}
    task_ids = sorted({m.get("task_id", "") for m in members})
    return {
        "tenant_id": key[0],
        "repository_scope": key[1] or None,
        "problem_type": key[2],
        "evidence": key[3],
        "failure_stage": key[4],
        "language": key[5],
        "path_type": key[6],
        "distinct_tasks": len(task_ids),
        "task_ids": task_ids,
        "signals": members,
        "signal_ids": [m.get("id", "") for m in members],
        "confidence": max((m.get("confidence", 0.0) for m in members), default=0.0),
        "cross_repo": len(repos) > 1,
    }


def cluster_experiences(experiences=(), events=()) -> List[Dict[str, Any]]:
    """Group experience/event signals along the plan's clustering dimensions."""
    signals = _signals_from_experiences(experiences) + _signals_from_events(events)
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for signal in signals:
        groups.setdefault(_cluster_key(signal), []).append(signal)
    return [_summarize_cluster(key, members) for key, members in groups.items()]


def _change_type(cluster: Dict[str, Any]) -> str:
    stage = cluster["failure_stage"]
    by_category = {
        exp.MISSED_ISSUE: hyp.RULE_ADD,
        exp.FALSE_POSITIVE: hyp.RULE_EXCEPTION,
        exp.BAD_FIX: hyp.RULE_TIGHTEN,
        exp.ACCEPTED: hyp.NO_CHANGE,
        AGENT_FAILURE: hyp.PROCEDURE_PROPOSAL,
        TOOL_FAILURE: hyp.TOOL_PROPOSAL,
        CRITIC_REJECTION: hyp.PROCEDURE_PROPOSAL,
        REPAIR_GATE_FAILURE: hyp.PROCEDURE_PROPOSAL,
        QUALITY_DRIFT: hyp.RULE_TIGHTEN,
    }
    if stage in by_category:
        return by_category[stage]
    # Unexplainable signals: keep the evidence but decide no_change.
    return hyp.NO_CHANGE


def _root_cause(cluster: Dict[str, Any], change_type: str) -> str:
    problem = cluster["problem_type"] or "the reviewed change"
    n = cluster["distinct_tasks"]
    stage = cluster["failure_stage"]
    if change_type == hyp.RULE_ADD:
        return "Rule %s missed a real defect pattern across %d independent task(s)." % (problem, n)
    if change_type == hyp.RULE_EXCEPTION:
        return "Rule %s over-flagged a non-defect (false positive)." % problem
    if change_type == hyp.RULE_TIGHTEN:
        return "Rule %s is under-specified or a previous fix regressed." % problem
    if change_type == hyp.NO_CHANGE:
        return "No actionable defect pattern could be isolated from the available evidence."
    if change_type == hyp.TOOL_PROPOSAL:
        return "Repeated %s failures indicate a tooling gap." % stage
    return "Repeated %s failures indicate a procedure gap." % stage


def _rationale(cluster: Dict[str, Any], change_type: str) -> str:
    return (
        "Derived from %d corroborated signal(s) across %d distinct task(s) at "
        "stage '%s'; change_type=%s, language=%s, path_type=%s."
    ) % (
        len(cluster["signals"]), cluster["distinct_tasks"], cluster["failure_stage"],
        change_type, cluster["language"], cluster["path_type"],
    )


def _expected_effect(cluster: Dict[str, Any], change_type: str) -> Dict[str, Any]:
    if change_type == hyp.NO_CHANGE:
        return {"expected": "no behavior change"}
    if change_type in (hyp.PROCEDURE_PROPOSAL, hyp.TOOL_PROPOSAL):
        return {"expected": "human proposal for %s" % change_type}
    if change_type == hyp.RULE_ADD:
        return {"expected": "fewer missed issues for %s" % cluster["problem_type"]}
    if change_type == hyp.RULE_EXCEPTION:
        return {"expected": "fewer false positives for %s" % cluster["problem_type"]}
    return {"expected": "improved precision for %s" % cluster["problem_type"]}


def _evaluation_requirements(cluster: Dict[str, Any], change_type: str) -> Dict[str, Any]:
    if change_type == hyp.NO_CHANGE:
        return {"expected": "no offline evaluation required"}
    return {
        "expected": "objective metric improves without regressing golden-regression",
        "dataset": "real-validation",
        "metric": "finding_accuracy",
    }


def _affected_domains(cluster: Dict[str, Any], change_type: str) -> List[str]:
    if change_type == hyp.NO_CHANGE:
        return ["none"]
    domains = [_domain(cluster["problem_type"])]
    if cluster["language"] != "unknown":
        domains.append(cluster["language"])
    return sorted(set(domains))


def build_hypothesis(
    cluster: Dict[str, Any],
    *,
    manual_confirms: Optional[Any] = None,
    case_ids_by_experience: Optional[Dict[str, List]] = None,
) -> Dict[str, Any]:
    """Turn one cluster into a structured Hypothesis (status stays draft)."""
    manual_confirms = set(manual_confirms or [])
    case_ids_by_experience = case_ids_by_experience or {}
    change_type = _change_type(cluster)
    risk = hyp.compute_risk(
        change_type,
        lowers_severity=(change_type == hyp.RULE_EXCEPTION),
        affects_high_risk_rule=_is_high_risk_rule(cluster["problem_type"]),
        cross_repo=cluster["cross_repo"],
    )
    case_ids: List = []
    for signal_id in cluster["signal_ids"]:
        case_ids.extend(case_ids_by_experience.get(signal_id, []) or [])
    manual = any(s["id"] in manual_confirms for s in cluster["signals"])
    result = hyp.new_hypothesis(
        tenant_id=cluster["tenant_id"],
        problem_type=cluster["problem_type"] or "unknown",
        failure_signature=cluster["evidence"] or cluster["failure_stage"] or "unknown",
        root_cause=_root_cause(cluster, change_type),
        change_type=change_type,
        repository_scope=cluster["repository_scope"],
        expected_effect=_expected_effect(cluster, change_type),
        affected_domains=_affected_domains(cluster, change_type),
        risk_level=risk,
        permissions=[],
        evaluation_requirements=_evaluation_requirements(cluster, change_type),
        rationale=_rationale(cluster, change_type),
        evidence_ids=cluster["signal_ids"],
        status=hyp.STATUS_DRAFT,
        source_case_ids=sorted(set(case_ids)),
        source_task_ids=cluster["task_ids"],
    )
    result["provenance"]["manual_confirmed"] = manual
    return result


def reflect(
    experiences=(),
    events=(),
    *,
    manual_confirms: Optional[Any] = None,
    case_ids_by_experience: Optional[Dict[str, List]] = None,
) -> List[Dict[str, Any]]:
    """Cluster signals and emit one structured Hypothesis per cluster."""
    return [
        build_hypothesis(
            cluster,
            manual_confirms=manual_confirms,
            case_ids_by_experience=case_ids_by_experience,
        )
        for cluster in cluster_experiences(experiences, events)
    ]


def disposition(hypothesis: Dict[str, Any], *, min_distinct_tasks: int = 2) -> str:
    """Classify how a generated Hypothesis should proceed.

    Returns one of ``no_change``, ``manual_review``, ``auto_materialize`` or
    ``insufficient_evidence``.  A single anonymous signal therefore never maps
    to ``auto_materialize`` unless it was explicitly authorized by a manual
    confirmation.
    """
    change_type = hypothesis.get("change_type")
    if change_type == hyp.NO_CHANGE:
        return "no_change"
    if hyp.is_manual_proposal(change_type):
        return "manual_review"
    if hypothesis.get("risk_level") == hyp.RISK_HIGH:
        return "manual_review"
    provenance = hypothesis.get("provenance") or {}
    if provenance.get("manual_confirmed"):
        return "auto_materialize"
    tasks = provenance.get("source_task_ids") or []
    if len(set(tasks)) >= min_distinct_tasks:
        return "auto_materialize"
    return "insufficient_evidence"
