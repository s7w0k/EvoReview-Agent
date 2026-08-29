"""Security Agent (plan §2.2, §12).

A genuinely observation-driven deep loop:

    security_rule_scan
      -> no risk?           -> Final
      -> semantic_scan
      -> evidence weak?     -> trace_dataflow (static source->sink)
      -> context needed?    -> inspect_context
      -> Final

Each step makes the *next* tool depend on the previous observation, and the
final artifact is built only from those observations.
"""
from typing import Any, Dict, List, Optional

from ..finding_identity import canonical_family
from ..models import Severity
from .base import BaseLoopAgent
from .stepper import (
    PlanTracker, final_action, observations, result_findings,
    tool_action, tool_results,
)

_RULE_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
_EVIDENCE_MIN = 20


def _merge_findings(groups: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        for finding in group:
            key = "%s:%s:%s" % (
                canonical_family(str(finding.get("rule_id", ""))),
                finding.get("path"), finding.get("line"))
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


def _finding_key(finding: Dict[str, Any]) -> str:
    return "%s:%s:%s" % (
        finding.get("rule_id"), finding.get("path"), finding.get("line"))


def _needs_dataflow(finding: Dict[str, Any]) -> bool:
    return len(str(finding.get("evidence") or "")) < _EVIDENCE_MIN


def choose_security_tool(state: Dict[str, Any],
                         findings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the next ``{tool, args}`` driven by the accumulated observations."""
    sd = state.setdefault("_sd", {})
    if not tool_results(state, "security_rule_scan"):
        return {"tool": "security_rule_scan", "args": {}}
    if not findings:
        return None
    if not tool_results(state, "semantic_scan"):
        return {"tool": "semantic_scan", "args": {}}
    for f in findings:
        key = _finding_key(f)
        rec = sd.setdefault(key, {"stage": 0})
        if rec["stage"] == 0 and _needs_dataflow(f):
            rec["stage"] = 1
            return {"tool": "trace_dataflow", "args": {"finding": f}}
        if rec["stage"] == 1:
            rec["stage"] = 2
            return {"tool": "inspect_context", "args": {"finding": f}}
        rec["stage"] = 2
    return None


class SecurityAgent(BaseLoopAgent):
    agent_id = "security-agent"
    capabilities = ("code-review", "security-review", "static-analysis", "semantic-analysis")
    task_type = "review.security"
    artifact_type = "security-findings"
    tool_allowlist = (
        "inspect_diff", "security_rule_scan", "semantic_scan",
        "trace_dataflow", "inspect_context",
    )

    def __init__(self, max_steps: Optional[int] = None,
                 timeout_seconds: Optional[int] = None, **kwargs):
        # The deepen loop (rule scan + semantic + per-finding dataflow/context)
        # needs more than the default 4-step budget.
        super().__init__(max_steps or 24, timeout_seconds or 90, **kwargs)

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        objective = str(task.get("objective") or task.get("task_type")
                        or "security review of changed lines")
        state = dict(task)
        state["objective"] = objective
        state["plan"] = None
        return state

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self._last_state = state
        plan = PlanTracker(
            state, str(state.get("objective")),
            ["scan security rules", "confirm via semantic scan",
             "trace dataflow / inspect context"], confidence=0.9,
        )
        rule = result_findings(tool_results(state, "security_rule_scan")) or []
        if not tool_results(state, "security_rule_scan"):
            plan.begin("security_rule_scan")
            return tool_action("security_rule_scan", {})
        if not rule:
            plan.complete("scan security rules")
            return final_action(findings=[])
        if not tool_results(state, "semantic_scan"):
            plan.begin("semantic_scan")
            return tool_action("semantic_scan", {})
        semantic = result_findings(tool_results(state, "semantic_scan")) or []
        merged = _merge_findings([rule, semantic])
        decision = choose_security_tool(state, merged)
        if decision is None:
            plan.complete("trace dataflow / inspect context")
            return final_action(findings=merged, agent_id=self.agent_id)
        plan.begin("tool:" + decision["tool"])
        return tool_action(decision["tool"], dict(decision["args"]))

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        rule = result_findings(tool_results(state, "security_rule_scan")) or []
        semantic = result_findings(tool_results(state, "semantic_scan")) or []
        merged = _merge_findings([rule, semantic])
        # enrich each finding with any dataflow / context evidence observed
        for f in merged:
            for tool in ("trace_dataflow", "inspect_context"):
                hits = tool_results(state, tool)
                for hit in hits:
                    if hit.get("rule_id") == f.get("rule_id"):
                        f.setdefault("deep_evidence", {})[tool] = {
                            "reached": hit.get("reached"),
                            "sources": hit.get("sources"),
                            "sinks": hit.get("sinks"),
                            "risk_relevant": hit.get("risk_relevant"),
                            "guard_ok": hit.get("guarded"),
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


__all__ = ["SecurityAgent", "choose_security_tool"]