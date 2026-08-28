"""Security Agent (plan §12).

A real two-step loop: run the deterministic security rule scan, then -- when
rules signal -- re-confirm with the semantic analyzer and merge.  Step N+1
reads step N's observation, and the final artifact is built only from those
observations, so later decisions genuinely depend on earlier ones.
"""
from typing import Any, Dict, List

from ..models import Severity
from .base import BaseLoopAgent
from .stepper import (
    PlanTracker, final_action, observations, result_findings,
    tool_action, tool_results,
)

_RULE_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}


def _merge_findings(groups: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        for finding in group:
            key = "%s:%s:%s" % (
                finding.get("rule_id"), finding.get("path"), finding.get("line"))
            current = merged.get(key)
            # keep the higher-severity / higher-confidence copy
            if current is None or _severity_rank(finding) <= _severity_rank(current):
                merged[key] = finding
            elif finding.get("confidence", 0) > current.get("confidence", 0):
                merged[key] = finding
    return sorted(merged.values(), key=lambda f: (
        _severity_rank(f), f.get("path", ""), int(f.get("line", 0))))


def _severity_rank(finding: Dict[str, Any]) -> int:
    try:
        return _RULE_ORDER[Severity(str(finding.get("severity", "low")))]
    except Exception:
        return _RULE_ORDER[Severity.LOW]


class SecurityAgent(BaseLoopAgent):
    agent_id = "security-agent"
    capabilities = ("code-review", "security-review", "static-analysis", "semantic-analysis")
    task_type = "review.security"
    artifact_type = "security-findings"
    tool_allowlist = ("inspect_diff", "security_rule_scan", "semantic_scan")

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        objective = str(task.get("objective") or task.get("task_type")
                        or "security review of changed lines")
        state = dict(task)
        state["objective"] = objective
        state["plan"] = None
        return state

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        plan = PlanTracker(
            state, str(state.get("objective")),
            ["scan security rules", "confirm via semantic scan"], confidence=0.9,
        )
        obs = observations(state)

        if not obs:
            plan.begin("security_rule_scan")
            return tool_action("security_rule_scan", {})

        if len(obs) == 1:
            hits = result_findings(tool_results(state, "security_rule_scan"))
            if hits:
                plan.revise(["confirm rule signals with the semantic analyzer"],
                            "rule hits need confirmation").marker("scan security rules")
                return tool_action("semantic_scan", {})
            plan.marker("scan security rules").complete("confirm via semantic scan")
            return final_action(findings=[])

        # We have the rule scan plus (optionally) the semantic scan results.
        rule = result_findings(tool_results(state, "security_rule_scan")) or []
        semantic = result_findings(tool_results(state, "semantic_scan")) or []
        merged = _merge_findings([rule, semantic])
        plan.complete("confirm via semantic scan")
        return final_action(findings=merged, agent_id=self.agent_id)

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        rule = result_findings(tool_results(state, "security_rule_scan")) or []
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


__all__ = ["SecurityAgent"]