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

        change_types = set(summary.get("change_types") or [])
        sensitive = set(summary.get("sensitive_paths") or [])
        new_inputs = bool(summary.get("new_external_inputs"))
        control_flow = bool(summary.get("control_flow_changes"))
        test_changes = bool(summary.get("test_changes"))
        level = str(risk.get("level") or "low")

        # -- specialist selection -------------------------------------------
        security = self._want_security(change_types, sensitive, new_inputs, rationale)
        reliability = self._want_reliability(
            change_types, control_flow, summary, rationale)
        if not security and not reliability:
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

        # -- critic (conditional, plan §8.2) --------------------------------
        high_risk = (level == "high")
        want_critic = high_risk or self._want_critic(summary, risk)
        critic_id: str = ""
        if want_critic and len(specialist_ids) > 0 and _has_agent(ctx, "critic-agent"):
            critic_id = TaskID("critic")
            nodes.append(PlannedTask(
                task_id=critic_id, agent_id="critic-agent",
                task_type="critique.findings",
                objective="challenge and reflect on the collected findings",
                dependencies=list(specialist_ids), priority=8,
                critical=high_risk,
            ))

        # -- verifier (conditional) -----------------------------------------
        verifier_deps = [critic_id] if critic_id else list(specialist_ids)
        want_verifier = high_risk or bool(
            summary.get("expected_findings") or new_inputs) or len(specialist_ids) > 0
        verifier_id: str = ""
        if want_verifier and verifier_deps and _has_agent(ctx, "verifier-agent"):
            verifier_id = TaskID("verifier")
            nodes.append(PlannedTask(
                task_id=verifier_id, agent_id="verifier-agent",
                task_type="verify.findings",
                objective="independently verify the findings",
                dependencies=verifier_deps, priority=6,
                critical=high_risk,
            ))

        # -- fix (only when remediation is allowed) -------------------------
        fix_allowed = bool((ctx.execution_policy or {}).get("remediation")
                           or (ctx.execution_policy or {}).get("fix_policy"))
        if fix_allowed and verifier_id and _has_agent(ctx, "fix-agent"):
            nodes.append(PlannedTask(
                task_id=TaskID("fix"), agent_id="fix-agent",
                task_type="fix.generate",
                objective="generate a verified repair for the findings",
                dependencies=[verifier_id], priority=4, serial=True,
                critical=False,
            ))

        return PlanningDecision(
            tasks=nodes, rationale_codes=rationale,
            confidence=0.95 if (security or reliability) else 0.5,
        )

    # -- decision helpers ---------------------------------------------------
    def _want_security(self, change_types, sensitive, new_inputs, rationale) -> bool:
        if new_inputs:
            rationale.append("NEW_EXTERNAL_INPUT")
        if "authentication" in change_types or (sensitive & {"auth", "authentication"}):
            rationale.append("AUTH_CHANGE")
        if change_types & {"database", "sql"}:
            rationale.append("DATABASE_CHANGE")
        if any("security" in str(p).lower() for p in sensitive):
            rationale.append("SECURITY_SENSITIVE_FILE")
        return bool(new_inputs) or bool(
            change_types & {"authentication", "database", "sql", "security"})

    def _want_reliability(self, change_types, control_flow, summary, rationale) -> bool:
        if change_types & {"exception", "error-handling"}:
            rationale.append("EXCEPTION_PATH_CHANGED")
            return True
        if change_types & {"concurrency", "async", "threading"}:
            rationale.append("CONCURRENCY_CHANGE")
            return True
        if change_types & {"runtime", "resource", "io"}:
            rationale.append("RESOURCE_LIFECYCLE_CHANGED")
            return True
        if control_flow:
            rationale.append("CONTROL_FLOW_CHANGED")
            return True
        if change_types & {"test"}:
            rationale.append("TEST_CHANGED")
            return bool(summary.get("test_changes"))
        return False

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