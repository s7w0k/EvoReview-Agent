"""Staged canary and automatic quality rollback (closed-loop WP6)."""
from typing import Any, Dict, List, Optional

# (canary_percent, minimum cumulative tasks) per stage.  100% promotes to active.
CANARY_STAGES = ((5, 50), (20, 200), (50, 500), (100, None))

ROLLED_BACK = "rolled_back"


def next_stage(current_percent: int) -> Optional[tuple]:
    """Return the next ``(percent, min_tasks)`` stage, or None at 100%."""
    for percent, min_tasks in CANARY_STAGES:
        if current_percent < percent:
            return (percent, min_tasks)
    return None


def should_advance(
    current_percent: int, tasks_since_stage: int, gates_passed: bool,
) -> bool:
    """Whether the canary may advance to its next stage.

    Advancement is never time-based: every stage must have seen enough tasks and
    passed its own gates.
    """
    stage = next_stage(current_percent)
    if stage is None:
        return False
    percent, min_tasks = stage
    if min_tasks is not None and tasks_since_stage < min_tasks:
        return False
    return bool(gates_passed)


def technical_rollback_reasons(
    deployment: Dict[str, Any],
    recent_results: List[Dict[str, Any]],
    *,
    stable_p95_ms: Optional[float] = None,
    hard_cost_budget: Optional[float] = None,
) -> List[str]:
    """Return technical (reliability/safety) rollback reasons, if any."""
    reasons: List[str] = []
    # Consecutive 3 execution failures.
    streak = 0
    for result in recent_results:
        if result.get("failed"):
            streak += 1
            if streak >= 3:
                reasons.append("candidate failed 3 times consecutively")
                break
        else:
            streak = 0

    window = recent_results[-50:]
    if window:
        errors = sum(1 for result in window if result.get("failed"))
        if errors / len(window) > 0.02:
            reasons.append("recent 50-task error rate exceeded 2%")

    if any(result.get("unauthorized_permission") for result in recent_results):
        reasons.append("unauthorized tool or permission request")

    latencies = sorted(
        result.get("latency_ms") for result in recent_results
        if result.get("latency_ms") is not None
    )
    if latencies and stable_p95_ms:
        p95 = latencies[int(len(latencies) * 0.95)]
        if p95 > stable_p95_ms * 1.5:
            reasons.append("p95 latency exceeded stable by 50%")

    if hard_cost_budget is not None and any(
        (result.get("cost") or 0.0) > hard_cost_budget for result in recent_results
    ):
        reasons.append("per-task cost exceeded hard budget")

    if any(result.get("isolation_anomaly") for result in recent_results):
        reasons.append("data or tenant isolation anomaly")

    if any(result.get("artifact_mismatch") for result in recent_results):
        reasons.append("candidate artifact fingerprint mismatch")

    return reasons


def quality_rollback_reasons(
    deployment: Dict[str, Any], metrics: Optional[Dict[str, Any]],
) -> List[str]:
    """Return quality (correctness/trust) rollback reasons, if any."""
    metrics = metrics or {}
    reasons: List[str] = []

    if metrics.get("high_risk_missed", 0) >= 1:
        reasons.append("confirmed high-risk miss")

    stable_fp = metrics.get("stable_fp_rate")
    candidate_fp = metrics.get("candidate_fp_rate")
    if stable_fp is not None and candidate_fp is not None:
        if candidate_fp > stable_fp + 0.02:
            reasons.append("false-positive rate exceeded stable by 2pp")

    stable_accept = metrics.get("stable_accept_rate")
    candidate_accept = metrics.get("candidate_accept_rate")
    if stable_accept is not None and candidate_accept is not None:
        if candidate_accept < stable_accept - 0.05:
            reasons.append("human accept rate below stable by 5pp")

    stable_clean = metrics.get("stable_clean_accuracy")
    candidate_clean = metrics.get("candidate_clean_accuracy")
    if stable_clean is not None and candidate_clean is not None:
        if candidate_clean < stable_clean - 0.02:
            reasons.append("clean accuracy dropped by more than 2pp")

    if metrics.get("repair_failure_rate_up", False):
        reasons.append("repair failure rate significantly increased")

    if metrics.get("golden_probe_failed", False):
        reasons.append("golden core capability online probe failed")

    return reasons
