"""Critic Agent = CriticAgent.challenge + ReflectionAgent.reflect (plan §14).

Read-only by design.  It challenges findings, detects duplicates/conflicts via a
deterministic tool, reflects on evidence quality, and only ever returns a
critique artifact -- it never delegates to a specialist directly (those go
through the Coordinator's replan loop).
"""
from typing import Any, Dict, List

from .base import BaseLoopAgent
from .stepper import PlanTracker, final_action, observations, tool_action

_DANGEROUS_TTL = ("disable validation", "ignore error", "catch all")


def _reflect(finding: Dict[str, Any]) -> Dict[str, Any]:
    evidence = str(finding.get("evidence") or "")
    explanation = str(finding.get("explanation") or "")
    fix = str(finding.get("fix") or "")
    rule_id = str(finding.get("rule_id") or "")
    actionable = bool(fix and not any(token in fix for token in _DANGEROUS_TTL))
    accepted = bool(evidence) and len(explanation) > 10
    missing_evidence = accepted and len(evidence) < 20
    return {
        "accepted": accepted,
        "missing_evidence": missing_evidence,
        "actionable_fix": actionable,
        "rejected": not accepted,
    }


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

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(task)
        state["findings"] = list(state.get("findings") or task.get("input", {}).get("findings") or [])
        state["objective"] = str(task.get("objective") or "critique and reflect on findings")
        state["plan"] = None
        return state

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        findings = list(state.get("findings") or [])
        plan = PlanTracker(state, str(state.get("objective")), [
            "detect duplicate/conflicting findings", "reflect on evidence quality"],
            confidence=0.9)

        if not observations(state):
            plan.begin("compare_peer_findings")
            return tool_action("compare_peer_findings", {"findings": findings})

        plan.marker("detect duplicate/conflicting findings").complete(
            "reflect on evidence quality")
        return final_action(agent_id=self.agent_id)

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        task = self._last_task
        findings = list(task.get("findings")
                       or (task.get("input") or {}).get("findings") or [])

        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        questions: List[str] = []
        missing_evidence: List[str] = []
        replan_requests: List[Dict[str, Any]] = []

        for finding in findings:
            appraisal = _reflect(finding)
            rule_id = str(finding.get("rule_id") or "?")
            key = "%s:%s:%s" % (rule_id, finding.get("path"), finding.get("line"))
            if appraisal["accepted"]:
                accepted.append(finding)
                if appraisal["missing_evidence"]:
                    missing_evidence.append(key)
                    replan_requests.append({
                        "node": "verify.findings",
                        "rule_id": rule_id, "reason": "insufficient evidence",
                    })
                if not appraisal["actionable_fix"]:
                    questions.append(key)
            else:
                rejected.append(finding)

        return {
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "accepted_findings": accepted,
            "rejected_findings": rejected,
            "questions": questions,
            "missing_evidence": missing_evidence,
            "replan_requests": replan_requests,
        }


__all__ = ["CriticAgent"]