"""Multi-Agent Evolution Attribution (plan §12).

Maps recorded signals to failure-attribution codes, so a degraded review can be
traced back to the exact component (planner routing / graph dependency / replan
targeting / critic/verifier blind spots / shallow specialist loop / tool pick).
Only structured codes are produced -- never raw reasoning.
"""
from typing import Any, Dict, List

FAILURE_ATTRIBUTION = {
    "PLANNER_ROUTING_MISS": "a specialist that should have been routed was skipped",
    "PLANNER_OVER_ROUTING": "too many specialists were routed for a trivial diff",
    "GRAPH_DEPENDENCY_ERROR": "a task graph edge was malformed or inconsistent",
    "REPLAN_TARGET_ERROR": "a replan request resolved to the wrong agent/action",
    "REPLAN_INSUFFICIENT": "replan happened but still lacked the needed evidence",
    "CRITIC_MISS": "the critic failed to flag a genuine evidence gap",
    "CRITIC_FALSE_CHALLENGE": "the critic challenged a finding without cause",
    "VERIFIER_MISS": "the verifier accepted a finding it could not reproduce",
    "VERIFIER_FALSE_REJECTION": "the verifier rejected a finding with real evidence",
    "SPECIALIST_LOOP_TOO_SHALLOW": "a specialist loop stopped before reaching evidence",
    "TOOL_SELECTION_ERROR": "the wrong tool was invoked for the evidence need",
}


def attribute_failure(signals: List[Dict[str, Any]]) -> List[str]:
    """Return attribution codes for a list of signal dicts (deterministic)."""
    codes: List[str] = []
    for s in signals:
        code = s.get("code")
        if not code or code not in FAILURE_ATTRIBUTION:
            continue
        if code not in codes:
            codes.append(str(code))
    return codes


def explain(code: str) -> str:
    return FAILURE_ATTRIBUTION.get(code, "unknown attribution code")


def emit_attribution(events: Dict[str, Any]) -> Dict[str, Any]:
    """Build an attribution summary from a run's recorded events (plan §9/§12)."""
    signals: List[Dict[str, Any]] = []
    for domain, items in (events or {}).items():
        for item in (items or []):
            if isinstance(item, dict) and item.get("code") in FAILURE_ATTRIBUTION:
                signals.append(item)
    codes = attribute_failure(signals)
    return {
        "codes": codes,
        "explanations": {c: explain(c) for c in codes},
        "attributed_events": len(codes),
    }


__all__ = ["FAILURE_ATTRIBUTION", "attribute_failure", "explain",
           "emit_attribution"]