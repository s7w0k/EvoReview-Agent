"""Verifier Agent = EvidenceAgent + VerifierAgent (plan §15).

Independent by design: instead of trusting the specialist's conclusion it runs
the shared semantic analyzer itself and re-derives each finding's rule signature,
producing per-finding ``verified`` + ``confidence`` decisions.  Loop step 2
depends on step 1's semantic observation.
"""
from typing import Any, Dict, List

from .base import BaseLoopAgent
from .stepper import (
    PlanTracker, final_action, observations, result_findings, tool_action,
    tool_results,
)

_RULE_PREFIXES = ("SEC-", "REL-")


def _finding_key(finding: Dict[str, Any]) -> str:
    return "%s:%s:%s" % (
        finding.get("rule_id"), finding.get("path"), finding.get("line"))


class VerifierAgent(BaseLoopAgent):
    agent_id = "verifier-agent"
    capabilities = ("finding-verification", "evidence-validation", "test-execution")
    task_type = "verify.findings"
    artifact_type = "verification-report"
    tool_allowlist = (
        "inspect_diff", "semantic_scan", "verify_rule_signature",
        "semantic_verify", "run_targeted_test", "inspect_evidence",
        "cross_check_finding",
    )

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(task)
        state["findings"] = list(state.get("findings")
                                 or (task.get("input") or {}).get("findings") or [])
        state["objective"] = str(task.get("objective") or "independently verify findings")
        state["plan"] = None
        return state

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        plan = PlanTracker(
            state, str(state.get("objective")),
            ["gather independent semantic evidence", "derive verification decisions"],
            confidence=0.9)
        if not observations(state):
            plan.begin("semantic_scan")
            return tool_action("semantic_scan", {})
        plan.marker("gather independent semantic evidence").complete(
            "derive verification decisions")
        return final_action(agent_id=self.agent_id)

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        task = self._last_task
        findings = list(task.get("findings")
                        or (task.get("input") or {}).get("findings") or [])
        semantic = result_findings(tool_results(state, "semantic_scan")) or []
        reproduced = {
            "%s:%s" % (item.get("path"), item.get("line")) for item in semantic
        }
        decisions: Dict[str, Any] = {}
        for finding in findings:
            key = _finding_key(finding)
            evidence = str(finding.get("evidence") or "")
            rule_id = str(finding.get("rule_id") or "")
            signature = rule_id.startswith(_RULE_PREFIXES)
            loc = "%s:%s" % (finding.get("path"), finding.get("line"))
            reproduced_here = loc in reproduced
            reasons = []
            if not evidence:
                reasons.append("missing evidence")
            if not signature and not reproduced_here:
                reasons.append("independent evidence could not reproduce the claim")
            verified = bool(evidence) and (signature or reproduced_here)
            base = float(finding.get("confidence", 0.8))
            confidence = max(0.0, min(1.0, base + (0.1 if reproduced_here else -0.05)))
            decisions[key] = {
                "finding_id": str(finding.get("id") or key),
                "verified": verified,
                "evidence": evidence,
                "verification_method": "rule-signature+semantic" if verified else "none",
                "confidence": round(confidence, 2),
                "failure_reason": "; ".join(reasons) if not verified else "",
            }
        return {
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "count": len(decisions),
            "decisions": decisions,
        }


__all__ = ["VerifierAgent"]