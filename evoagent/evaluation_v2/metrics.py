"""Detection + runtime + governance metrics for Evaluation Harness V2.

Detection metrics intentionally reuse the V1 scorer (``one_to_one_match`` and the
formulas in ``EndToEndEvaluationHarness``) so a reported F1 is directly comparable
to the historical 71.4% / 82.5% numbers.  The matcher is never modified by the V2
runner; only the evaluated system changes.
"""
import statistics
from typing import Any, Dict, List

from evoagent.evaluation_harness import (
    RULE_TO_CWE,
    EndToEndEvaluationHarness,
    one_to_one_match,
)

# Static instance so aggregate formulas are shared verbatim with V1.
_SCORER = EndToEndEvaluationHarness(line_tolerance=2)


def normalized_path(path: str) -> str:
    value = path.replace("\\", "/").strip()
    return value[2:] if value.startswith(("a/", "b/")) else value


def _field(value: Any, name: str, default=None):
    """Read a field from an object or a dict."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def score_case(case: dict, execution) -> Dict[str, Any]:
    """Score one case against its ground truth; keep the exact V1 result shape.

    ``execution`` is the adapter's :class:`EvaluationExecutionResult` (has
    ``findings`` (real ``Finding`` objects) plus telemetry fields).  Returns a
    ``_run_case``-shaped dict so ``EndToEndEvaluationHarness._accumulate /
    _metrics`` can aggregate it.
    """
    expected = list(case["expected_findings"])
    findings = list(_field(execution, "findings") or [])
    matches = one_to_one_match(expected, findings, _SCORER.line_tolerance)
    predicted = len(findings)
    tp = len(matches)
    fp = predicted - tp
    fn = len(expected) - tp
    severity_hits = 0
    high_hits = 0
    for match in matches:
        truth = expected[match.expected_index]
        finding = findings[match.predicted_index]
        severity_hit = finding.severity.value == str(truth["severity"]).lower()
        high = str(truth["severity"]).lower() in {"high", "critical"}
        severity_hits += int(severity_hit)
        high_hits += int(high)
    high_total = sum(
        str(item["severity"]).lower() in {"high", "critical"} for item in expected
    )
    result = {
        "id": case["id"],
        "repository": case["repository"],
        "pull_request": case["pull_request"],
        "split": case["split"],
        "expected": len(expected),
        "predicted": predicted,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "severity_hits": severity_hits,
        "high_total": high_total,
        "high_hits": high_hits,
        "clean_hit": bool((not expected) and (not findings)),
        "execution_success": bool(_field(execution, "success")),
        # V1 aggregation expects these repair fields even when no repairer is used.
        "repair_attempted": 0,
        "repair_passed": 0,
        "e2e_success": False,
        # telemetry carried alongside the V1 detection fields.
        "latency_ms": _field(execution, "latency_ms", 0.0),
        "agent_steps": _field(execution, "agent_steps", 0),
        "tool_calls": _field(execution, "tool_calls", 0),
        "recovery_attempts": _field(execution, "recovery_attempts", 0),
        "recovery_successes": _field(execution, "recovery_successes", 0),
        "policy_denials": _field(execution, "policy_denials", 0),
        "circuit_breaker_trips": _field(execution, "circuit_breaker_trips", 0),
        "timeouts": _field(execution, "timeouts", 0),
        "side_effect_blocks": _field(execution, "side_effect_blocks", 0),
        "decision_trace_created": _field(execution, "decision_trace_created", False),
        "replay_snapshot_created": _field(execution, "replay_snapshot_created", False),
        "trace_event_count": _field(execution, "trace_event_count", 0),
        "policy_id": _field(execution, "policy_id", ""),
        "policy_version": _field(execution, "policy_version", 0),
        "deployment_lane": _field(execution, "deployment_lane", ""),
        "resolved_policy": _field(execution, "resolved_policy", {}),
        "error": _field(execution, "error"),
        # Multi-agent execution proof (harness systems only; empty for reviewers).
        "collaboration": _field(execution, "collaboration", {}),
        "matches": [
            {
                "expected_index": match.expected_index,
                "predicted_index": match.predicted_index,
                "cwe": RULE_TO_CWE.get(findings[match.predicted_index].rule_id,
                                       findings[match.predicted_index].rule_id),
            }
            for match in matches
        ],
    }
    return result


def detection_metrics(case_results: List[dict]) -> Dict[str, Any]:
    """Aggregate detection metrics exactly as the V1 harness does."""
    totals = _SCORER._empty_totals()
    for result in case_results:
        _SCORER._accumulate(totals, result)
    return _SCORER._metrics(totals | {"cases": totals["cases"]})


def _percentile(values: List[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * len(ordered)))
    return round(ordered[index], 4)


def _collab(case_results: List[dict]) -> List[dict]:
    return [
        (r.get("collaboration") or {})
        for r in case_results if isinstance(r.get("collaboration"), dict)
    ]


def _collab_avg(case_results: List[dict], key: str) -> float:
    """Mean of a collaboration scalar across cases that report a collaboration."""
    values = [
        int(item.get(key) or 0)
        for item in _collab(case_results) if item.get(key) is not None
    ]
    return round(statistics.mean(values), 2) if values else 0.0


def _collab_agents(case_results: List[dict]) -> List[str]:
    """Distinct specialist agents observed across the collaboration summaries."""
    agents: List[str] = []
    for item in _collab(case_results):
        for agent in item.get("agents") or []:
            name = agent.get("agent") if isinstance(agent, dict) else str(agent)
            if name and name not in agents:
                agents.append(name)
    return agents


def runtime_metrics(case_results: List[dict]) -> Dict[str, Any]:
    """Aggregate runtime / governance telemetry across cases."""
    total = len(case_results)
    successes = [r for r in case_results if r.get("execution_success")]
    latency = [float(r["latency_ms"]) for r in case_results]
    rec_attempts = sum(int(r.get("recovery_attempts", 0)) for r in case_results)
    rec_successes = sum(int(r.get("recovery_successes", 0)) for r in case_results)
    return {
        "execution_success_rate": round(len(successes) / total, 4) if total else 0.0,
        "recovery_attempts": rec_attempts,
        "recovery_successes": rec_successes,
        "recovery_success_rate": round(rec_successes / rec_attempts, 4) if rec_attempts else 0.0,
        "avg_agent_steps": round(
            sum(int(r.get("agent_steps", 0)) for r in successes) / len(successes), 4
        ) if successes else 0.0,
        "avg_tool_calls": round(
            sum(int(r.get("tool_calls", 0)) for r in successes) / len(successes), 4
        ) if successes else 0.0,
        "p50_latency_ms": _percentile(latency, 0.50),
        "p95_latency_ms": _percentile(latency, 0.95),
        "tool_denials": sum(int(r.get("policy_denials", 0)) for r in case_results),
        "timeouts": sum(int(r.get("timeouts", 0)) for r in case_results),
        "side_effect_blocks": sum(int(r.get("side_effect_blocks", 0)) for r in case_results),
        "circuit_breaker_trips": sum(
            int(r.get("circuit_breaker_trips", 0)) for r in case_results),
        "decision_trace_coverage": round(
            sum(bool(r.get("decision_trace_created")) for r in case_results) / total, 4
        ) if total else 0.0,
        "replay_snapshot_coverage": round(
            sum(bool(r.get("replay_snapshot_created")) for r in case_results) / total, 4
        ) if total else 0.0,
        "avg_policy_version": round(
            statistics.mean(
                [int(r.get("policy_version", 0)) for r in case_results if r.get("policy_version")]
            ), 4
        ) if any(r.get("policy_version") for r in case_results) else 0,
        # Multi-agent DAG execution proof (harness systems only).  Averages over
        # the harness cases -- not the plain reviewers, which have no collab.
        "collaboration_rounds": _collab_avg(case_results, "dialogue_rounds"),
        "collaboration_messages": _collab_avg(case_results, "messages"),
        "activated_agents": _collab_agents(case_results),
        "collaboration_detected": round(
            sum(bool(r.get("collaboration")) for r in case_results) / total, 4
        ) if total else 0.0,
    }


def by_split(case_results: List[dict]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for split in ("validation", "holdout"):
        selected = [r for r in case_results if r.get("split") == split]
        if selected:
            output[split] = {
                "detection": detection_metrics(selected),
                "runtime": runtime_metrics(selected),
            }
    return output


def summarize(case_results: List[dict]) -> Dict[str, Any]:
    return {
        "cases": len(case_results),
        "detection": detection_metrics(case_results),
        "runtime": runtime_metrics(case_results),
        "by_split": by_split(case_results),
    }


__all__ = ["score_case", "detection_metrics", "runtime_metrics", "by_split", "summarize"]