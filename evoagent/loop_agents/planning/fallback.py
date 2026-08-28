"""Deterministic Fallback Planner (plan §4.6).

Used when the Semantic Planner proposal fails validation more than once.  It
keeps the review safe by defaulting to the well-understood specialist routing.
"""
from typing import Any, Dict, List

from .models import PlanningContext, PlanningDecision, PlannedTask

# Structured rationale codes for the fallback path (never raw CoT).
FALLBACK_RATIONALE_CODES = {
    "SECURITY_SENSITIVE": "route to security when the diff is security-sensitive",
    "RUNTIME_RELIABILITY": "route to reliability on runtime/reliability signals",
    "HIGH_RISK_VERIFY": "high-risk findings must be independently verified",
    "VERIFIED_FIX": "fix only follows a verified finding and an enabled fix policy",
}


class FallbackPlanner:
    """Repair planner: guarantees a valid, minimal task DAG."""

    def plan(self, ctx: PlanningContext) -> PlanningDecision:
        summary = ctx.semantic_summary or {}
        risk = ctx.risk_profile or {}
        rationale: List[str] = []
        nodes: List[PlannedTask] = []

        change_types = set(summary.get("change_types") or [])
        sensitive_paths = [str(p).lower() for p in (summary.get("sensitive_paths") or [])]
        security = bool(summary.get("estimated_risk") == "high" or
                        bool(change_types & {"authentication", "security", "sql",
                                             "database"}) or
                        any(k in p for p in sensitive_paths
                            for k in ("security", "auth")) or
                        bool(summary.get("new_external_inputs")))
        reliability = True  # always a baseline reliability pass
        specialist_ids: List[str] = []

        index = 0
        if security:
            nodes.append(PlannedTask(
                task_id="spec%d" % index, agent_id="security-agent",
                task_type="review.security", objective="security review",
                priority=10, critical=True))
            specialist_ids.append("spec%d" % index)
            rationale.append("SECURITY_SENSITIVE")
            index += 1
        nodes.append(PlannedTask(
            task_id="spec%d" % index, agent_id="reliability-agent",
            task_type="review.reliability", objective="reliability review",
            priority=10))
        specialist_ids.append("spec%d" % index)
        rationale.append("RUNTIME_RELIABILITY" if not security else "CLEAN_BASELINE")

        high_risk = security or str(risk.get("level")) == "high"
        critic_id = ""
        if high_risk:
            critic_id = "critic"
            nodes.append(PlannedTask(
                task_id=critic_id, agent_id="critic-agent",
                task_type="critique.findings",
                objective="challenge findings", dependencies=list(specialist_ids),
                priority=8, critical=True))
        verifier_deps = [critic_id] if critic_id else list(specialist_ids)
        verifier_id = "verifier"
        nodes.append(PlannedTask(
            task_id=verifier_id, agent_id="verifier-agent",
            task_type="verify.findings", objective="verify findings",
            dependencies=list(verifier_deps), priority=6, critical=high_risk))
        rationale.append("HIGH_RISK_VERIFY" if high_risk else "CLEAN")

        if (ctx.execution_policy or {}).get("fix_policy"):
            nodes.append(PlannedTask(
                task_id="fix", agent_id="fix-agent", task_type="fix.generate",
                objective="generate verified repair", dependencies=[verifier_id],
                priority=4, serial=True))
            rationale.append("VERIFIED_FIX")

        return PlanningDecision(tasks=nodes, rationale_codes=rationale,
                                confidence=0.8)


__all__ = ["FallbackPlanner", "FALLBACK_RATIONALE_CODES"]