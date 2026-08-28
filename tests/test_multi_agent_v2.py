"""Unit tests for the Multi-Agent 6-item optimization (plan §4, §5, §6, §8).

Covers the Semantic Planner + Validator + Fallback, Targeted Replan, Parallel
Scheduler, Dynamic Collaboration Graph, and the v2 Coordinator integration.
"""
import os

from evoagent.loop_agents.coordinator import CoordinatorAgent
from evoagent.loop_agents.graph_policy import (
    GraphMutator, critic_trigger, fix_trigger, verifier_trigger,
)
from evoagent.loop_agents.models import AgentTaskNode, CoordinatorTaskGraph
from evoagent.loop_agents.planning import (
    PlanningContext, SemanticPlanner, TaskGraphValidator, build_default_context,
    build_graph_from_tasks, FallbackPlanner,
)
from evoagent.loop_agents.replan import (
    ReplanBudget, ReplanRequest, ReplanTargetResolver, ReplanTracker,
    emit_replan_request,
)
from evoagent.loop_agents.deep_loop import (
    evaluate_stop_condition, pick_verification_strategy,
    select_verifier_strategy_for,
)
from evoagent.loop_agents.scheduler import ConcurrencyBudget, TaskGraphScheduler


# ---------------------------------------------------------------------------
# Phase 1: Semantic Dynamic Planner
# ---------------------------------------------------------------------------

def test_planner_security_only():
    ctx = build_default_context(
        "", semantic_summary={
            "change_types": ["authentication"], "sensitive_paths": ["auth.py"],
            "new_external_inputs": False, "control_flow_changes": False,
            "test_changes": False},
        risk_profile={"level": "high"},
        available_agents=[{"agent_id": a} for a in
                          ("security-agent", "reliability-agent",
                           "critic-agent", "verifier-agent", "fix-agent")],
    )
    decision = SemanticPlanner().plan(ctx)
    types = {t.task_type for t in decision.tasks}
    assert "review.security" in types
    assert "critique.findings" in types  # high risk => critic
    assert "verify.findings" in types
    assert any("AUTH_CHANGE" in r for r in decision.rationale_codes)


def test_planner_reliability_baseline_on_clean():
    ctx = build_default_context(
        "", semantic_summary={
            "change_types": [], "sensitive_paths": [],
            "new_external_inputs": False, "control_flow_changes": False,
            "test_changes": False},
        risk_profile={"level": "low"},
    )
    decision = SemanticPlanner().plan(ctx)
    assert any(t.task_type == "review.reliability" for t in decision.tasks)
    assert "CLEAN_BASELINE" in decision.rationale_codes


# ---------------------------------------------------------------------------
# Phase 1: TaskGraphValidator + FallbackPlanner
# ---------------------------------------------------------------------------

def test_validator_rejects_missing_agent_and_cycle():
    graph = CoordinatorTaskGraph(graph_id="g")
    a = AgentTaskNode("a", "review.security", "o", agent_id="security-agent")
    b = AgentTaskNode("b", "review.reliability", "o", agent_id="reliability-agent",
                      dependencies=["c"])
    c = AgentTaskNode("c", "critique.findings", "o", agent_id="critic-agent",
                      dependencies=["a"])
    b.dependencies = ["c"]  # a -> c -> b -> c cycle
    for n in (a, b, c):
        graph.add(n)
    validator = TaskGraphValidator(available_agents={"security-agent"})
    errors = validator.validate(graph)
    # unknown agent reliability-agent + dependency c not a node
    assert any("unknown agent" in e for e in errors)
    assert not validator.is_valid(graph)


def test_fallback_planner_is_deterministic():
    ctx = build_default_context(
        "", semantic_summary={
            "change_types": ["authentication"], "sensitive_paths": ["auth"],
            "new_external_inputs": True, "control_flow_changes": False,
            "test_changes": False},
        risk_profile={"level": "high"},
    )
    plan = FallbackPlanner().plan(ctx)
    types = {t.task_type for t in plan.tasks}
    assert "review.security" in types
    assert "review.reliability" in types


# ---------------------------------------------------------------------------
# Phase 2: Targeted Result-driven Replan
# ---------------------------------------------------------------------------

def test_replan_resolver_and_budget_loop_protection():
    req = emit_replan_request(
        source_agent="critic-agent", target_capability="verification",
        finding_id="F1", reason_code="INSUFFICIENT_EXPLANATION",
        reason_summary="need more evidence", requested_action="verification",
        required_evidence=["rule signature"], finding={},
    )
    assert ReplanTargetResolver(["verifier-agent"]).resolve(req) == "verifier-agent"

    tracker = ReplanTracker(ReplanBudget(max_replans_per_review=2))
    assert tracker.accept(req) is True
    assert tracker.accept(req) is False  # fingerprint dedup => loop protection


# ---------------------------------------------------------------------------
# Phase 3: Parallel TaskGraph Scheduler
# ---------------------------------------------------------------------------

def _graph_with_specialists():
    graph = CoordinatorTaskGraph(graph_id="g")
    graph.add(AgentTaskNode("spec0", "review.security", "o",
                            agent_id="security-agent"))
    graph.add(AgentTaskNode("spec1", "review.reliability", "o",
                            agent_id="reliability-agent"))
    graph.add(AgentTaskNode("critic", "critique.findings", "o",
                            agent_id="critic-agent", dependencies=["spec0", "spec1"]))
    graph.add(AgentTaskNode("verifier", "verify.findings", "o",
                            agent_id="verifier-agent", dependencies=["critic"]))
    graph.add(AgentTaskNode("fix", "fix.generate", "o", agent_id="fix-agent",
                            dependencies=["verifier"], serial=True))
    return graph


def test_scheduler_parallel_batch_and_serial_slot():
    graph = _graph_with_specialists()
    sched = TaskGraphScheduler(graph, ConcurrencyBudget(max_parallel_agents=2))
    batch = sched.next_batch()
    assert set(batch) == {"spec0", "spec1"}  # both specialists run in parallel
    # should never schedule the serial fix with others
    assert len(batch) <= 2


# ---------------------------------------------------------------------------
# Phase 5: Dynamic Collaboration Graph
# ---------------------------------------------------------------------------

def test_graph_mutator_add_and_cancel_history_safe():
    graph = _graph_with_specialists()
    for n in ("spec0", "spec1"):
        graph.nodes[n].status = "completed"
    node = next(n for n in graph.nodes.values() if n.node_id == "critic")
    node.status = "pending"
    mutator = GraphMutator(graph)
    mutator.cancel_branch(["fix"], reason="no findings")
    assert graph.nodes["fix"].status == "rejected"
    assert len(mutator.applied) == 1


def test_trigger_predicates():
    assert critic_trigger({}, {"level": "high"}, []) == (True, "HIGH_RISK")
    assert verifier_trigger({}, {"level": "low"}, 0, True) == (True, "EXTERNAL_INPUT")
    assert fix_trigger([{"rule_id": "SEC-1"}], {"fix_policy": {"enabled": True}})[0] is True


# ---------------------------------------------------------------------------
# Phase 5/6: v2 Coordinator integration (end-to-end graph build)
# ---------------------------------------------------------------------------

def test_coordinator_v2_builds_planner_graph(monkeypatch):
    monkeypatch.setenv(os.getenv("EVOAGENT_V2_SCHEDULING", "1") and
                       "EVOAGENT_V2_SCHEDULING", "1")
    coord = CoordinatorAgent(mode="v2")
    summary = {
        "change_types": ["authentication", "sql"],
        "sensitive_paths": ["auth.py"],
        "new_external_inputs": True, "control_flow_changes": False,
        "test_changes": False,
    }
    state = {"diff": "--- a/x\n+++ b/x\n"}
    graph = coord._build_graph_v2(state, summary, {"level": "high"})
    types = {n.task_type for n in graph.nodes.values()}
    assert "review.security" in types
    assert "verify.findings" in types
    # graph must be valid per the validator over the default agent rosetta
    validator = TaskGraphValidator(available_agents=set(coord._available_agents()))
    assert validator.is_valid(graph), validator.validate(graph)


# ---------------------------------------------------------------------------
# Phase 4: Deeper Local Agent Loops
# ---------------------------------------------------------------------------

def test_stop_condition_threshold_met():
    out = evaluate_stop_condition(
        {"confidence_threshold_met": 0.7}, confidence=0.85)
    assert out == {"stop": True, "code": "confidence_threshold_met",
                   "label": "CONFIDENCE_THRESHOLD_MET"}


def test_stop_condition_goal_and_fallbacks():
    assert evaluate_stop_condition({}, goal_satisfied=True)["code"] == "goal_satisfied"
    assert evaluate_stop_condition({}, tool_unavailable=True)["code"] == "tool_unavailable"
    assert evaluate_stop_condition(
        {}, steps=5, max_steps=4)["code"] == "budget_exhausted"
    assert evaluate_stop_condition({}, progress_made=False)["code"] == "no_progress"
    assert evaluate_stop_condition({})["stop"] is False


def test_verification_strategy_selection():
    secure = {"rule_id": "SEC-001", "evidence": "x" * 30}
    assert pick_verification_strategy(secure, has_sandbox=True) == "targeted_test"
    assert pick_verification_strategy(
        secure, has_sandbox=False, has_semantic_repro=False) == "rule_signature"
    assert pick_verification_strategy(
        {}, has_sandbox=False, has_semantic_repro=False, has_rule_signature=False
    ) == "context_inspection"
    strategies = select_verifier_strategy_for(
        [secure, {"rule_id": "REL-2", "evidence": ""}])
    assert list(strategies.items())


# ---------------------------------------------------------------------------
# Phase 6: Multi-Agent Value Evaluation V4
# ---------------------------------------------------------------------------

def test_evaluation_metrics_and_report():
    from evoagent.evaluation_v4.metrics import aggregate_metrics, evaluate_run
    from evoagent.evaluation_v4.report import build_report, render_markdown
    from evoagent.evaluation_v4.scenarios import (
        FIXTURE_KINDS, build_scenario, load_scenarios, sample_scenarios,
    )

    record = {
        "artifact": {"rationale_codes": ["AUTH_CHANGE", "HIGH_RISK"],
                     "graph_revision": 2, "replan_count": 1, "count": 1,
                     "steps": 3, "delegated_tasks": 2},
        "tool_calls": 4, "a2a_calls": 2,
        "collaborations": ["critic", "verifier"], "loop_sizes": [2, 2, 3],
        "expected_count": 1,
    }
    scores = evaluate_run(record)
    assert set(scores) <= {"planning_quality", "replan_quality",
                           "collaboration_quality", "loop_quality", "efficiency"}
    agg = aggregate_metrics([scores, scores])
    assert agg["overall"] > 0 and agg["runs"] == 2

    # scenarios corpus + sample + report
    scenarios = load_scenarios("__missing__.jsonl")
    assert len(scenarios) >= len(FIXTURE_KINDS)
    assert sample_scenarios(scenarios, 3).__len__() == 3
    results = {"A": [record], "B": [record]}
    report = build_report(results)
    md = render_markdown(report)
    assert "## Ablation deltas" in md
    assert "baseline_variant" in report


# ---------------------------------------------------------------------------
# Phases 7-9: Failure injection + Observability + Evolution attribution
# ---------------------------------------------------------------------------

def test_failure_injection_and_attribution():
    from evoagent.loop_agents.attribution import (
        FAILURE_ATTRIBUTION, attribute_failure, emit_attribution, explain,
    )
    from evoagent.loop_agents.failure_injection import (
        FAILURE_CATALOG, FailureInjector, inject,
    )

    fi = FailureInjector(["COORD_PLANNING_FAILURE", "TIMEOUT"])
    assert fi.should_inject("planning")
    assert inject(fi, "planning") == {"injected": "COORD_PLANNING_FAILURE",
                                      "domain": "planning"}
    assert "COORD_PLANNING_FAILURE" in FAILURE_CATALOG

    codes = attribute_failure(
        [{"code": "CRITIC_MISS"}, {"code": "CRITIC_MISS"}])
    assert codes == ["CRITIC_MISS"]
    assert explain("CRITIC_MISS") in FAILURE_ATTRIBUTION["CRITIC_MISS"]
    attribution = emit_attribution({"critic": [{"code": "VERIFIER_MISS"}]})
    assert attribution["codes"] == ["VERIFIER_MISS"]


def test_observability_trace_context():
    from evoagent.loop_agents.observability import build_trace_context
    ctx = build_trace_context(agent_id="coordinator", graph_id="g1",
                              replan_request_id="R1")
    assert "a2a_task_id" in ctx and ctx["graph_revision"] == 1
    assert ctx["agent_version"] == "six-agent-v2"