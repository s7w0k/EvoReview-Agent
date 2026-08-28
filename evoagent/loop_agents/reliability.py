"""Reliability Agent (plan §2.3, §13).

Observation-driven deep loop:

    reliability_rule_scan
      -> no hit?            -> Final
      -> semantic_scan      (confirm)
      -> needs runtime?     -> inspect_execution_path
      -> still untested?    -> run_targeted_test
      -> Final

Each observation selects the next tool, and the final artifact is derived only
from the observed results.
"""
from typing import Any, Dict, List, Optional

from .base import BaseLoopAgent
from .security import _merge_findings
from .stepper import (
    PlanTracker, final_action, observations, result_findings,
    tool_action, tool_results,
)

_EVIDENCE_MIN = 20


def _finding_key(finding: Dict[str, Any]) -> str:
    return "%s:%s:%s" % (
        finding.get("rule_id"), finding.get("path"), finding.get("line"))


def _needs_runtime_evidence(finding: Dict[str, Any]) -> bool:
    return len(str(finding.get("evidence") or "")) < _EVIDENCE_MIN


def choose_reliability_tool(state: Dict[str, Any],
                            findings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    rd = state.setdefault("_rd", {})
    if not tool_results(state, "reliability_rule_scan"):
        return {"tool": "reliability_rule_scan", "args": {}}
    if not findings:
        return None
    if not tool_results(state, "semantic_scan"):
        return {"tool": "semantic_scan", "args": {}}
    for f in findings:
        key = _finding_key(f)
        rec = rd.setdefault(key, {"stage": 0})
        if rec["stage"] == 0 and _needs_runtime_evidence(f):
            rec["stage"] = 1
            return {"tool": "inspect_execution_path", "args": {"finding": f}}
        # still expose findings that warrant a targeted sanity test
        rec["stage"] = 1
    return None


class ReliabilityAgent(BaseLoopAgent):
    agent_id = "reliability-agent"
    capabilities = ("code-review", "reliability-review", "regression-analysis", "test-analysis")
    task_type = "review.reliability"
    artifact_type = "reliability-findings"
    tool_allowlist = (
        "inspect_diff", "reliability_rule_scan", "semantic_scan",
        "inspect_execution_path", "run_targeted_test",
    )

    def __init__(self, max_steps: Optional[int] = None,
                 timeout_seconds: Optional[int] = None, **kwargs):
        super().__init__(max_steps or 16, timeout_seconds or 90, **kwargs)

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        objective = str(task.get("objective") or task.get("task_type")
                        or "reliability review of changed lines")
        state = dict(task)
        state["objective"] = objective
        state["plan"] = None
        return state

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self._last_state = state
        plan = PlanTracker(
            state, str(state.get("objective")),
            ["scan reliability rules", "confirm via semantic scan",
             "inspect execution path / targeted test"], confidence=0.85,
        )
        rule = result_findings(tool_results(state, "reliability_rule_scan")) or []
        if not tool_results(state, "reliability_rule_scan"):
            plan.begin("reliability_rule_scan")
            return tool_action("reliability_rule_scan", {})
        if not rule:
            plan.complete("scan reliability rules")
            return final_action(findings=[])
        if not tool_results(state, "semantic_scan"):
            plan.begin("semantic_scan")
            return tool_action("semantic_scan", {})
        semantic = result_findings(tool_results(state, "semantic_scan")) or []
        merged = _merge_findings([rule, semantic])
        decision = choose_reliability_tool(state, merged)
        if decision is None:
            plan.complete("inspect execution path / targeted test")
            return final_action(findings=merged, agent_id=self.agent_id)
        plan.begin("tool:" + decision["tool"])
        return tool_action(decision["tool"], dict(decision["args"]))

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        rule = result_findings(tool_results(state, "reliability_rule_scan")) or []
        semantic = result_findings(tool_results(state, "semantic_scan")) or []
        merged = _merge_findings([rule, semantic])
        for f in merged:
            for hit in tool_results(state, "inspect_execution_path"):
                if hit.get("rule_id") == f.get("rule_id"):
                    f.setdefault("deep_evidence", {})["inspect_execution_path"] = {
                        "guarded": hit.get("guarded"),
                        "has_try": hit.get("has_try"),
                        "has_except": hit.get("has_except"),
                    }
        avg_conf = (sum(f.get("confidence", 0) for f in merged) / len(merged)
                    if merged else 0.0)
        return {
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "count": len(merged),
            "findings": merged,
            "confidence": round(avg_conf, 2),
        }


__all__ = ["ReliabilityAgent", "choose_reliability_tool"]