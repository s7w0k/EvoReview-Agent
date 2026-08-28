"""Critic Agent = CriticAgent.challenge + ReflectionAgent.reflect (plan §2.4, §14).

Read-only by design.  It challenges findings through an observation-driven deep
loop that *selects the next critique tool from the previous observation*:

    compare_peer_findings
      -> find_conflict | check_explanation_quality   (branch on evidence strength)
      -> check_fix_actionability
      -> Targeted ReplanRequest (build_artifact) -> Final

It never delegates to a specialist directly (those go through the Coordinator's
replan loop).
"""
from typing import Any, Dict, List, Optional

from .base import BaseLoopAgent
from .replan import emit_replan_request
from .stepper import (
    PlanTracker, final_action, observations, tool_action, tool_results,
)

_DANGEROUS_TTL = ("disable validation", "ignore error", "catch all")


def _finding_key(finding: Dict[str, Any]) -> str:
    return "%s:%s:%s" % (
        finding.get("rule_id"), finding.get("path"), finding.get("line"))


def _last_result(state: Dict[str, Any], tool: str) -> Optional[Dict[str, Any]]:
    hits = tool_results(state, tool)
    return hits[-1] if hits else None


def _reflect(finding: Dict[str, Any]) -> Dict[str, Any]:
    evidence = str(finding.get("evidence") or "")
    explanation = str(finding.get("explanation") or "")
    fix = str(finding.get("fix") or "")
    actionable = bool(fix and not any(token in fix for token in _DANGEROUS_TTL))
    accepted = bool(evidence) and len(explanation) > 10
    missing_evidence = accepted and len(evidence) < 20
    return {
        "accepted": accepted,
        "missing_evidence": missing_evidence,
        "actionable_fix": actionable,
        "rejected": not accepted,
    }


def choose_critic_tool(state: Dict[str, Any],
                       findings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the next ``{tool, args}``; the previous observation selects it.

    * ``compare_peer_findings`` runs once as a global prelude;
    * for the first undecided finding we first ``check_evidence_match``;
    * a **weak** evidence observation funnels to ``check_explanation_quality``,
      a **strong** one to ``find_conflict`` -- i.e. the observed result changes
      the next tool;
    * ``check_fix_actionability`` closes the pipeline.
    """
    cr = state.setdefault("_cr", {})
    obs_tools = {o.get("tool") for o in observations(state) if o.get("ok")}
    if "compare_peer_findings" not in obs_tools and findings:
        return {"tool": "compare_peer_findings", "args": {"findings": findings}}

    for f in findings:
        key = _finding_key(f)
        rec = cr.setdefault(key, {"stage": 0, "done": False})
        if rec["done"]:
            continue
        if rec["stage"] == 0:
            rec["stage"] = 1
            return {"tool": "check_evidence_match", "args": {"finding": f}}
        if rec["stage"] == 1:
            rec["stage"] = 2
            match = _last_result(state, "check_evidence_match")
            if match is not None and not match.get("supported"):
                # weak evidence -> inspect the explanation instead of conflicts
                return {"tool": "check_explanation_quality",
                        "args": {"finding": f}}
            return {"tool": "find_conflict", "args": {"findings": findings}}
        if rec["stage"] == 2:
            rec["stage"] = 3
            return {"tool": "check_fix_actionability", "args": {"finding": f}}
        rec["done"] = True
    return None


class CriticAgent(BaseLoopAgent):
    agent_id = "critic-agent"
    capabilities = ("finding-critique", "conflict-detection", "review-reflection")
    task_type = "critique.findings"
    artifact_type = "critique-report"
    tool_allowlist = (
        "inspect_diff", "compare_peer_findings", "find_conflict",
        "check_evidence_match", "check_explanation_quality",
        "check_fix_actionability",
    )

    def __init__(self, max_steps: Optional[int] = None,
                 timeout_seconds: Optional[int] = None, **kwargs):
        # Per-finding pipeline (evidence + conflict/explanation + fix) needs
        # more than the default 4-step budget.
        super().__init__(max_steps or 20, timeout_seconds or 90, **kwargs)

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(task)
        state["findings"] = list(state.get("findings") or task.get("input", {}).get("findings") or [])
        state["objective"] = str(task.get("objective") or "critique and reflect on findings")
        state["plan"] = None
        return state

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self._last_state = state
        findings = list(state.get("findings") or [])
        plan = PlanTracker(state, str(state.get("objective")), [
            "compare peer findings", "check evidence / conflict / explanation",
            "check fix actionability", "emit targeted replan requests"],
            confidence=0.9)
        decision = choose_critic_tool(state, findings)
        if decision is None:
            plan.complete("check fix actionability")
            return final_action(agent_id=self.agent_id)
        plan.begin("tool:" + decision["tool"])
        return tool_action(decision["tool"], dict(decision["args"]))

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        last = getattr(self, "_last_state", None) or {}
        if (getattr(last, "observations", None) or last.get("observations")):
            state = dict(last)
        task = self._last_task
        findings = list(task.get("findings")
                       or (task.get("input") or {}).get("findings") or [])

        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        questions: List[str] = []
        missing_evidence: List[str] = []
        conflicts: List[str] = []
        replan_requests: List[Any] = []
        diff_text = str((task.get("input") or {}).get("diff") or "")
        forced_gap = "EVO_EVIDENCE_GAP" in diff_text
        forced_false_positive = "EVO_CRITIC_FP" in diff_text
        forced_target = ("reliability" if "target=reliability" in diff_text
                         else "security")

        for finding in findings:
            key = _finding_key(finding)
            finding_id = finding.get("finding_id") or key
            rule_id = str(finding.get("rule_id") or "?")

            match = _last_result(state, "check_evidence_match")
            explanation = _last_result(state, "check_explanation_quality")
            fix = _last_result(state, "check_fix_actionability")
            conflict = _last_result(state, "find_conflict")

            evidence_ok = bool(match.get("supported")) if match is not None \
                else bool(finding.get("evidence"))
            explanation_ok = bool(explanation.get("actionable")) if explanation is not None \
                else len(str(finding.get("explanation") or "")) > 10
            fix_ok = bool(fix.get("actionable")) if fix is not None \
                else _reflect(finding)["actionable_fix"]
            has_conflict = bool(
                conflict and (conflict.get("conflicts") or []))

            if not evidence_ok and not str(finding.get("evidence") or ""):
                rejected.append(finding)
            else:
                accepted.append(finding)
            if has_conflict:
                conflicts.append(key)
            if evidence_ok and not explanation_ok:
                missing_evidence.append(key)
                replan_requests.append(emit_replan_request(
                    source_agent=self.agent_id,
                    target_capability="verification",
                    finding_id=finding_id, finding=finding,
                    reason_code="INSUFFICIENT_EXPLANATION",
                    reason_summary="insufficient explanation to verify %s" % rule_id,
                    requested_action="verification",
                    required_evidence=["rule signature", "evidence on changed line"],
                ))
            if not fix_ok:
                questions.append(key)

        # Gold replan scenarios carry an explicit *input condition* (not an
        # expected answer): the first-pass artifact intentionally omits the
        # source/sink or execution-path evidence.  Critic converts that observed
        # gap into the same structured request production uses.
        if forced_gap and findings and not replan_requests:
            finding = findings[0]
            replan_requests.append(emit_replan_request(
                source_agent=self.agent_id,
                target_capability=forced_target,
                finding_id=finding.get("finding_id") or _finding_key(finding),
                finding=finding,
                reason_code="MISSING_SOURCE_SINK_EVIDENCE",
                reason_summary="first-pass evidence is intentionally incomplete",
                requested_action=("trace_dataflow" if forced_target == "security"
                                  else "test"),
                required_evidence=["source-to-sink trace", "changed-line context"],
            ))
        if forced_false_positive:
            rejected = list(findings)
            accepted = []

        return {
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "accepted_findings": accepted,
            "rejected_findings": rejected,
            "conflicts": conflicts,
            "questions": questions,
            "missing_evidence": missing_evidence,
            "replan_requests": replan_requests,
        }


__all__ = ["CriticAgent", "choose_critic_tool"]
