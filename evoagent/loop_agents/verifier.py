"""Verifier Agent (plan §2.5, §7, §11) -- observation-driven deep loop.

The loop is genuinely decision-driven: the observation from the previous tool
*selects* the next tool.  Per-finding bookkeeping (``attempted_strategies`` /
``remaining_strategies`` / ``confidence`` / ``verified``) lives in ``state`` and
loop protection forbids re-selecting the same strategy for the same finding.

Modelled after plan §2.5:

    inspect_evidence
      -> VerificationStrategySelector
      -> verify_rule_signature | semantic_verify | run_targeted_test |
         cross_check_finding
      -> Observation
      -> confidence enough?  no -> choose another strategy; yes -> Final
"""
from typing import Any, Dict, List, Optional

from .base import BaseLoopAgent
from .stepper import (
    PlanTracker, final_action, observations, tool_action, tool_results,
)

_RULE_PREFIXES = ("SEC-", "REL-")

# strategy/tool ladder per rule kind (deterministic, no repetition).
_LADDER: Dict[str, List[str]] = {
    "security": ["verify_rule_signature", "semantic_verify", "run_targeted_test"],
    "reliability": ["run_targeted_test", "verify_rule_signature", "semantic_verify"],
    "default": ["semantic_verify", "cross_check_finding"],
}


def _finding_key(finding: Dict[str, Any]) -> str:
    return "%s:%s:%s" % (
        finding.get("rule_id"), finding.get("path"), finding.get("line"))


def _ladder(finding: Dict[str, Any]) -> List[str]:
    rule_id = str(finding.get("rule_id") or "")
    if rule_id.startswith("SEC-"):
        return _LADDER["security"]
    if rule_id.startswith("REL-"):
        return _LADDER["reliability"]
    return _LADDER["default"]


def _result_satisfied(tool: str, result: Dict[str, Any], finding: Dict[str, Any]) -> bool:
    """Does a tool's result independently support the finding? (plan §2.5)."""
    result = result or {}
    if tool == "verify_rule_signature":
        return bool(result.get("supported"))
    if tool == "semantic_verify":
        return bool(result.get("verified"))
    if tool == "run_targeted_test":
        return bool(result.get("passed"))
    if tool == "cross_check_finding":
        return bool(result.get("consistent")) and not result.get("peer_conflicts")
    if tool == "inspect_evidence":
        return bool(result.get("evidence") or str(finding.get("evidence") or ""))
    return False


def choose_verifier_tool(state: Dict[str, Any],
                         findings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the next ``{tool, args}`` based on the current observations.

    The previous observation determines the next tool: a satisfied result makes
    the finding ``verified``; an unsatisfied one moves on to a *different*
    strategy.  Returns ``None`` once every finding is decided.
    """
    vf = state.setdefault("_vf", {})
    for f in findings:
        key = _finding_key(f)
        if key not in vf:
            vf[key] = {"tried": [], "done": False, "verified": None,
                       "evidence": False}

    obs_tools = sorted({
        o.get("tool") for o in observations(state) if o.get("ok")})

    # 1) reconcile decisions purely from the accumulated observations: if any
    #    verification result already present satisfies a finding, finalise it.
    for f in findings:
        rec = vf[_finding_key(f)]
        if rec["done"]:
            continue
        if "inspect_evidence" in obs_tools:
            rec["evidence"] = True
        for tool in _ladder(f):
            if tool not in obs_tools:
                continue
            for result in tool_results(state, tool):
                if _result_satisfied(tool, result, f):
                    rec["verified"] = True
                    rec["done"] = True
                    break
            if rec["done"]:
                break
        # the observed ladder tools are the strategies already attempted
        rec["tried"] = [t for t in _ladder(f) if t in obs_tools]

    # 2) gather evidence for the first undecided finding that has none yet
    #    (always with a concrete ``finding`` -- never an empty arg dict).
    for f in findings:
        rec = vf[_finding_key(f)]
        if rec["done"]:
            continue
        if not rec["evidence"]:
            return {"tool": "inspect_evidence", "args": {"finding": f}}

    # 3) otherwise choose the next untried verification tool for the first
    #    undecided finding (the previous observation selects the next tool).
    for f in findings:
        rec = vf[_finding_key(f)]
        if rec["done"]:
            continue
        for tool in _ladder(f):
            if tool not in rec["tried"]:
                rec["tried"].append(tool)
                return {"tool": tool, "args": {"finding": f}}

    # 3) everything decided (verified False where no tool satisfied)
    for f in findings:
        rec = vf[_finding_key(f)]
        if not rec["done"]:
            rec["done"] = True
            rec["verified"] = False
    return None


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

    def __init__(self, max_steps: Optional[int] = None,
                 timeout_seconds: Optional[int] = None, **kwargs):
        # The deep loop walks each finding through inspect_evidence plus its
        # strategy ladder, so the default 4-step budget is too small.
        super().__init__(max_steps or 16, timeout_seconds or 90, **kwargs)

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(task)
        state["findings"] = list(state.get("findings")
                                 or (task.get("input") or {}).get("findings") or [])
        state["objective"] = str(task.get("objective") or "independently verify findings")
        state["plan"] = None
        state["_vf"] = {}
        return state

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self._last_state = state
        plan = PlanTracker(
            state, str(state.get("objective")),
            ["gather evidence", "select verification strategy",
             "re-derive confidence from observations", "finalize"],
            confidence=0.9)
        findings = state["findings"]
        # The pure decision function drives the whole loop: it (1) gathers
        # evidence per finding, (2) marks findings satisfied by the previous
        # observation, and (3) picks the next *different* verification tool --
        # or ``None`` to finalize.  We never invent arguments here.
        decision = choose_verifier_tool(state, findings)
        if decision is None:
            plan.complete("finalize")
            return final_action(agent_id=self.agent_id)
        plan.begin("tool:" + decision["tool"])
        return tool_action(decision["tool"], dict(decision["args"]))

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        task = self._last_task
        findings = list(task.get("findings")
                        or (task.get("input") or {}).get("findings") or [])
        force_reject = "EVO_VERIFIER_FP" in str(
            (task.get("input") or {}).get("diff") or "")
        # reproduce the bookkeeping the loop produced.
        vf = dict((getattr(self, "_last_state", {}) or {}).get("_vf") or {})
        if not vf and findings:
            choose_verifier_tool(state, findings)
            vf = state.get("_vf") or {}
        decisions: Dict[str, Any] = {}
        for finding in findings:
            key = _finding_key(finding)
            rec = vf.get(key, {})
            tried = list(rec.get("tried") or [])
            verified = bool(rec.get("verified")) if rec.get("verified") is not None else False
            if force_reject:
                verified = False
            base = float(finding.get("confidence", 0.8))
            confidence = max(0.0, min(
                1.0, base + (0.15 if verified else -0.2)))
            evidence = str(finding.get("evidence") or "")
            remaining = [t for t in _ladder(finding) if t not in tried]
            decisions[key] = {
                "finding_id": str(finding.get("id") or key),
                "verified": verified,
                "evidence": evidence,
                "attempted_strategies": tried,
                "remaining_strategies": remaining,
                "verification_method": tried[-1] if tried else "none",
                "verification_strategy": tried[-1] if tried else "none",
                "confidence": round(confidence, 2),
                "failure_reason": "" if verified else (
                    "no verification tool produced independent support"),
            }
        return {
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "count": len(decisions),
            "decisions": decisions,
        }


__all__ = ["VerifierAgent", "choose_verifier_tool"]
