"""Mechanism-specific Evaluation V4 metrics derived from real runtime traces."""
from typing import Any, Dict, List


QUALITY_DIMS = (
    "detection_quality", "planning_quality", "replan_quality",
    "collaboration_quality", "loop_quality",
)


def load_outcome(record: Dict[str, Any]) -> Dict[str, Any]:
    artifact = record.get("artifact") or {}
    return {
        "accepted_count": int(artifact.get("count") or 0),
        "expected_count": int(record.get("expected_count") or 0),
        "called_agents": list(artifact.get("called_agents")
                              or record.get("collaborations") or []),
        "expected_agents": list(record.get("expected_agents") or []),
        "forbidden_agents": list(record.get("forbidden_agents") or []),
        "replan_count": int(artifact.get("replan_count") or 0),
        "replan_targets": list(artifact.get("replan_targets") or []),
        "expected_replan": bool(record.get("expected_replan")),
        "expected_replan_target": record.get("expected_replan_target"),
        "graph_shapes": list(artifact.get("graph_shapes") or []),
        "required_graph_edges": list(record.get("required_graph_edges") or []),
        "loop_steps": dict(artifact.get("loop_steps_by_agent") or {}),
        "parallel_batches": list(artifact.get("parallel_batches") or []),
        "duration_ms": float(record.get("duration_ms") or 0.0),
        "tool_calls": int(record.get("tool_calls") or 0),
        "a2a_calls": int(record.get("a2a_calls") or 0),
        "category": str(record.get("category") or ""),
    }


def _f1(tp: int, fp: int, fn: int) -> float:
    precision = tp / float(tp + fp) if tp + fp else 1.0
    recall = tp / float(tp + fn) if tp + fn else 1.0
    return (2 * precision * recall / (precision + recall)
            if precision + recall else 0.0)


def evaluate_run(record: Dict[str, Any]) -> Dict[str, float]:
    o = load_outcome(record)
    detection = float(o["accepted_count"] == o["expected_count"])
    called, expected = set(o["called_agents"]), set(o["expected_agents"])
    forbidden = set(o["forbidden_agents"])
    planning = _f1(len(called & expected), len(called & forbidden),
                   len(expected - called))

    if o["expected_replan"]:
        target_ok = (not o["expected_replan_target"] or
                     o["expected_replan_target"] in o["replan_targets"])
        replan = float(o["replan_count"] > 0 and target_ok and detection == 1.0)
    else:
        replan = float(o["replan_count"] == 0)

    actual_edges = {(dep, node["node_id"]) for node in o["graph_shapes"]
                    for dep in node.get("dependencies") or []}
    required = {tuple(edge) for edge in o["required_graph_edges"]}
    edges_ok = not required or required <= actual_edges
    collaboration = float(edges_ok and expected <= called)

    specialist_steps = [steps for agent, steps in o["loop_steps"].items()
                        if agent in ("security-agent", "reliability-agent")]
    if o["category"] == "deep_loop":
        loop_quality = float(detection == 1.0 and specialist_steps
                             and max(specialist_steps) >= 3)
    else:
        loop_quality = float(bool(o["loop_steps"]) or not expected)

    return {
        "detection_quality": round(detection, 4),
        "planning_quality": round(planning, 4),
        "replan_quality": round(replan, 4),
        "collaboration_quality": round(collaboration, 4),
        "loop_quality": round(loop_quality, 4),
        "latency_ms": round(o["duration_ms"], 4),
        "tool_calls": float(o["tool_calls"]),
        "a2a_calls": float(o["a2a_calls"]),
    }


def aggregate_metrics(scores: List[Dict[str, float]]) -> Dict[str, Any]:
    n = max(1, len(scores))
    averaged = {key: round(sum(s.get(key, 0.0) for s in scores) / n, 4)
                for key in QUALITY_DIMS + ("latency_ms", "tool_calls", "a2a_calls")}
    averaged["overall"] = round(
        sum(averaged[d] for d in QUALITY_DIMS) / len(QUALITY_DIMS), 4)
    averaged["runs"] = len(scores)
    return averaged


__all__ = ["QUALITY_DIMS", "load_outcome", "evaluate_run", "aggregate_metrics"]
