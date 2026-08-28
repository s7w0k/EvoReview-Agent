"""Evaluation V4 metrics (plan §9.5).

Scoring is deterministic over a normalised outcome record so it can be applied
to a single run (event emit) or aggregated across a scenario corpus.  Five
dimensions are reported: Planning Quality, Replan Quality, Collaboration
Quality, Loop Quality, Efficiency.
"""
from typing import Any, Dict, List


def load_outcome(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise one run outcome into the fields the metrics read."""
    artifact = record.get("artifact") or {}
    decision = record.get("decision") or {}
    return {
        "planning_rationale_codes": list(artifact.get("rationale_codes") or []),
        "graph_revision": int(artifact.get("graph_revision") or decision.get(
            "graph_revision") or 1),
        "replan_count": int(artifact.get("replan_count") or decision.get(
            "replan_count") or 0),
        "collaborations": list(record.get("collaborations")
                               or artifact.get("collaborations") or []),
        "loop_steps": int(artifact.get("steps") or record.get("steps") or 0),
        "tool_calls": int(record.get("tool_calls") or 0),
        "a2a_calls": int(record.get("a2a_calls") or 0),
        "accepted_count": int(artifact.get("count") or 0),
        "expected_count": int(decision.get("expected_count") or record.get(
            "expected_count") or 0),
        "graph_mutations": list(record.get("graph_mutations") or []),
        "loop_sizes": list(record.get("loop_sizes") or []),
        "delegated_tasks": int(artifact.get("delegated_tasks") or 0),
    }


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def evaluate_run(record: Dict[str, Any]) -> Dict[str, float]:
    """Compute the five V4 quality dimensions for one outcome record."""
    o = load_outcome(record)

    # Planning quality: structured rationale density + a validated graph.
    planning = _ratio(float(len(o["planning_rationale_codes"])), 1.0)
    planning = min(1.0, planning / 3.0)

    # Replan quality: target precision + low request volume.
    expected = max(1, o["expected_count"])
    replan = 1.0 - min(1.0, float(o["replan_count"]) / max(2, expected))

    # Collaboration quality: evidence that downstream stages ran (mutations +
    # collaborations) normalised.
    collab_coverage = _ratio(float(len(o["collaborations"])), float(expected))
    collab = min(1.0, collab_coverage + _ratio(float(len(o["graph_mutations"])), float(expected)))

    # Loop quality: agents converge within a reasonable loop size.
    loop_sizes = o["loop_sizes"] or [1]
    avg_loop = sum(loop_sizes) / float(len(loop_sizes)) if loop_sizes else 1.0
    loop_quality = 1.0 - min(1.0, max(0.0, avg_loop - 2.0) / 6.0)

    # Efficiency: fewer A2A round-trips relative to expected tasks.
    efficiency = _ratio(float(expected), float(max(1, expected + o["a2a_calls"])))

    return {
        "planning_quality": planning,
        "replan_quality": replan,
        "collaboration_quality": collab,
        "loop_quality": loop_quality,
        "efficiency": efficiency,
    }


def aggregate_metrics(scores: List[Dict[str, float]]) -> Dict[str, Any]:
    """Average the per-run dimension scores."""
    dims = ("planning_quality", "replan_quality", "collaboration_quality",
            "loop_quality", "efficiency")
    sums: Dict[str, float] = {d: 0.0 for d in dims}
    for score in scores:
        for d in dims:
            sums[d] += score.get(d, 0.0)
    n = max(1, len(scores))
    averaged = {d: round(sums[d] / n, 4) for d in dims}
    averaged["overall"] = round(sum(sums[d] for d in dims) / (n * len(dims)), 4)
    averaged["runs"] = len(scores)
    return averaged


__all__ = ["load_outcome", "evaluate_run", "aggregate_metrics"]