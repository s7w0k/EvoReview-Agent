"""Reliability Agent (plan §13).

Runs the deterministic reliability rule scan, then the semantic analyzer when
rules signal, merging the two observations into the final artifact.
"""
from typing import Any, Dict

from .base import BaseLoopAgent
from .security import _merge_findings
from .stepper import (
    PlanTracker, final_action, observations, result_findings,
    tool_action, tool_results,
)


class ReliabilityAgent(BaseLoopAgent):
    agent_id = "reliability-agent"
    capabilities = ("code-review", "reliability-review", "regression-analysis", "test-analysis")
    task_type = "review.reliability"
    artifact_type = "reliability-findings"
    tool_allowlist = ("inspect_diff", "reliability_rule_scan", "semantic_scan")

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        objective = str(task.get("objective") or task.get("task_type")
                        or "reliability review of changed lines")
        state = dict(task)
        state["objective"] = objective
        state["plan"] = None
        return state

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        plan = PlanTracker(
            state, str(state.get("objective")),
            ["scan reliability rules", "confirm via semantic scan"], confidence=0.85,
        )
        obs = observations(state)

        if not obs:
            plan.begin("reliability_rule_scan")
            return tool_action("reliability_rule_scan", {})

        if len(obs) == 1:
            hits = result_findings(tool_results(state, "reliability_rule_scan"))
            if hits:
                plan.revise(["confirm reliability signals with the semantic analyzer"],
                            "rule hits need confirmation").marker("scan reliability rules")
                return tool_action("semantic_scan", {})
            plan.marker("scan reliability rules").complete("confirm via semantic scan")
            return final_action(findings=[])

        rule = result_findings(tool_results(state, "reliability_rule_scan")) or []
        semantic = result_findings(tool_results(state, "semantic_scan")) or []
        merged = _merge_findings([rule, semantic])
        plan.complete("confirm via semantic scan")
        return final_action(findings=merged, agent_id=self.agent_id)

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        rule = result_findings(tool_results(state, "reliability_rule_scan")) or []
        semantic = result_findings(tool_results(state, "semantic_scan")) or []
        merged = _merge_findings([rule, semantic])
        avg_conf = (sum(f.get("confidence", 0) for f in merged) / len(merged)
                    if merged else 0.0)
        return {
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "count": len(merged),
            "findings": merged,
            "confidence": round(avg_conf, 2),
        }


__all__ = ["ReliabilityAgent"]