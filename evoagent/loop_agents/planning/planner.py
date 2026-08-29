"""Semantic Dynamic Planner (plan §4).

The Coordinator's rule-route upgrade: given a semantic change summary and risk
profile it proposes a *structured* task DAG -- who runs, in what order, guarded
by determinism and evidence requirements.  The proposal is always validated by
the :class:`TaskGraphValidator` before execution (plan §4.5).
"""
from typing import Any, Dict, List

from .models import PlanningContext, PlanningDecision, PlannedTask

# task type -> agent that owns it (must match the A2A cards).
TASK_OWNER = {
    "review.security": "security-agent",
    "review.reliability": "reliability-agent",
    "critique.findings": "critic-agent",
    "verify.findings": "verifier-agent",
    "fix.generate": "fix-agent",
}


def _has_agent(ctx: PlanningContext, agent_id: str) -> bool:
    return any(
        (card.get("agent_id") == agent_id) or (agent_id in card.get("id", ""))
        for card in ctx.available_agents
    ) or not ctx.available_agents  # empty availability = allow everything locally


class SemanticPlanner:
    """Deterministic, evidence-driven task graph proposer."""

    def __init__(self, *, max_nodes: int = 12, max_depth: int = 6):
        self.max_nodes = max_nodes
        self.max_depth = max_depth

    def plan(self, ctx: PlanningContext) -> PlanningDecision:
        summary = ctx.semantic_summary or {}
        risk = ctx.risk_profile or {}
        rationale: List[str] = []

        level = str(risk.get("level") or "low")

        # -- specialist selection -------------------------------------------
        # Shared predicates guarantee planner == fallback == profiler routing and
        # dual-route high-risk diffs (plan §3.2 / §Phase 2).  risk_profile.agents
        # and risk_profile.domains are strong inputs -- the planner must never
        # silently drop the profiler's specialist suggestion.
        from .risk_signals import should_route_reliability, should_route_security
        security = should_route_security(risk, summary)
        reliability = should_route_reliability(risk, summary)
        if security and reliability:
            rationale.append("DUAL_ROUTE_HIGH_OR_CROSS")
        if "security-agent" in (risk.get("agents") or []):
            rationale.append("PROFILER_SUGGESTED_SECURITY")
        if "reliability-agent" in (risk.get("agents") or []):
            rationale.append("PROFILER_SUGGESTED_RELIABILITY")
        change_types = set(summary.get("change_types") or [])
        if "authentication" in change_types:
            rationale.append("AUTH_CHANGE")
        if not (security or reliability):
            # clean / test-only PR still gets a lightweight reliability pass so
            # every PR has an evidence baseline.
            reliability = True
            rationale.append("CLEAN_BASELINE")

        # -- build the DAG --------------------------------------------------
        nodes: List[PlannedTask] = []
        specialist_ids: List[str] = []
        index = 0
        if security and _has_agent(ctx, "security-agent"):
            spec = TaskID("spec", index)
            specialist_ids.append(spec)
            nodes.append(PlannedTask(
                task_id=spec, agent_id="security-agent",
                task_type="review.security",
                objective="review changed lines for security",
                priority=10,
                required_evidence=["rule signature", "evidence on changed line"],
                stop_condition={"goal_satisfied": True, "confidence_threshold_met": 0.7},
                critical=(level == "high"),
            ))
            index += 1
        if reliability and _has_agent(ctx, "reliability-agent"):
            spec = TaskID("spec", index)
            specialist_ids.append(spec)
            nodes.append(PlannedTask(
                task_id=spec, agent_id="reliability-agent",
                task_type="review.reliability",
                objective="review changed lines for reliability",
                priority=10,
                required_evidence=["rule signature", "evidence on changed line"],
                stop_condition={"goal_satisfied": True, "confidence_threshold_met": 0.7},
            ))
            index += 1

        # The initial graph is deliberately minimal.  Critic, Verifier and Fix
        # are inserted only after real specialist artifacts are observed by the
        # runtime GraphPolicy.  Planning them here would make the feature look
        # dynamic while preserving a static specialists->critic->verifier->fix DAG.

        return PlanningDecision(
            tasks=nodes, rationale_codes=rationale,
            confidence=0.95 if (security or reliability) else 0.5,
        )

    # -- decision helpers ---------------------------------------------------
    def _want_critic(self, summary, risk) -> bool:
        # Disagreement / low confidence / novel rule are strong triggers.
        if len(summary.get("change_types") or []) >= 3:
            return True
        return False


def TaskID(kind: str, index: int = 0) -> str:
    if kind == "critic":
        return "critic"
    if kind == "verifier":
        return "verifier"
    if kind == "fix":
        return "fix"
    return "spec%d" % index


def build_default_context(
    diff: str = "", *, semantic_summary: Dict[str, Any],
    risk_profile: Dict[str, Any], available_agents: List[Dict[str, Any]] = (),
    execution_policy: Dict[str, Any] = None, prior_artifacts: List[Dict[str, Any]] = (),
) -> PlanningContext:
    ctx = PlanningContext(
        objective="coordinate a multi-agent code review",
        changed_files=list(semantic_summary.get("changed_files") or []),
        semantic_summary=dict(semantic_summary),
        risk_profile=dict(risk_profile),
        available_agents=list(available_agents),
        execution_policy=dict(execution_policy or {}),
        prior_artifacts=list(prior_artifacts),
    )
    return ctx


__all__ = [
    "TASK_OWNER", "SemanticPlanner", "build_default_context", "TaskID",
]
