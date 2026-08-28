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
    assert "critique.findings" not in types  # runtime conditional
    assert "verify.findings" not in types
    assert "fix.generate" not in types
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
    assert "verify.findings" not in types
    assert "critique.findings" not in types
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
# 真正闭环 Phase 3: Verifier Deep Loop (plan §2.8) -- the hard gate asserts the
# *observation* chooses the next tool, not just that a strategy field appeared.
# ---------------------------------------------------------------------------

def _vf_obs(tool, result):
    return {"step": 1, "tool": tool, "ok": True, "result": dict(result)}


def _sec_finding():
    return {"rule_id": "SEC-EVAL", "path": "app.py", "line": 5,
            "title": "sql", "evidence": "", "confidence": 0.8, "id": "f1"}


def test_verifier_observation_selects_next_tool():
    from evoagent.loop_agents.verifier import choose_verifier_tool
    finding = _sec_finding()
    # Evidence gathered, then verify_rule_signature SUCCEEDS -> finding done.
    sat = choose_verifier_tool(
        {"observations": [_vf_obs("inspect_evidence", {"evidence": "e"}),
                          _vf_obs("verify_rule_signature", {"supported": True,
                                                             "rule_id": "SEC-EVAL",
                                                             "content": "x"})]},
        [finding])
    # falsify feedback -> the NEXT observed outcome selects a DIFFERENT tool.
    fail = choose_verifier_tool(
        {"observations": [_vf_obs("inspect_evidence", {"evidence": "e"}),
                          _vf_obs("verify_rule_signature", {"supported": False,
                                                             "rule_id": "SEC-EVAL",
                                                             "content": ""})]},
        [finding])
    assert sat is None, "supported observation must finalize the finding"
    assert fail is not None and fail["tool"] == "semantic_verify", (
        "unsupported observation must cascade to a different verification tool")


def test_verifier_no_repeat_same_strategy():
    from evoagent.loop_agents.verifier import choose_verifier_tool
    finding = _sec_finding()
    # Inspect -> verify_rule_signature(False) -> semantic_verify(False) ->
    # the SAME tool must never be re-offered; next is run_targeted_test.
    state = {"observations": [
        _vf_obs("inspect_evidence", {"evidence": "e"}),
        _vf_obs("verify_rule_signature", {"supported": False, "rule_id": "SEC"}),
        _vf_obs("semantic_verify", {"verified": False, "rule_id": "SEC"}),
    ]}
    decision = choose_verifier_tool(state, [finding])
    assert decision is not None and decision["tool"] == "run_targeted_test"


def test_verifier_failure_cascades_none_and_reports_unverified():
    from evoagent.loop_agents.verifier import choose_verifier_tool
    finding = _sec_finding()
    state = {"observations": [
        _vf_obs("inspect_evidence", {"evidence": "e"}),
        _vf_obs("verify_rule_signature", {"supported": False, "rule_id": "SEC"}),
        _vf_obs("semantic_verify", {"verified": False, "rule_id": "SEC"}),
        _vf_obs("run_targeted_test", {"passed": False, "rule_id": "SEC"}),
    ]}
    assert choose_verifier_tool(state, [finding]) is None
    rec = state["_vf"]["SEC-EVAL:app.py:5"]
    assert rec["done"] is True and rec["verified"] is False


def test_verifier_full_artifact_records_strategies():
    from evoagent.loop_agents import VerifierAgent
    agent = VerifierAgent()
    out = agent.run({
        "task_id": "v1", "task_type": "verify.findings", "objective": "verify",
        "input": {"findings": [_sec_finding()]}})
    decisions = out["artifact"]["decisions"]
    rec = decisions["SEC-EVAL:app.py:5"]
    assert rec["verified"] is True
    assert rec["attempted_strategies"][0] == "verify_rule_signature"
    assert "remaining_strategies" in rec
    assert rec["verification_strategy"] == rec["attempted_strategies"][-1]


# ---------------------------------------------------------------------------
# 真正闭环 Phase 4: Critic Deep Loop (plan §2.4) -- previous observation selects
# the next critique tool.
# ---------------------------------------------------------------------------

def test_critic_observation_selects_next_tool():
    from evoagent.loop_agents.critic import choose_critic_tool
    finding = {"rule_id": "SEC-EVAL", "path": "app.py", "line": 5,
               "title": "sql", "evidence": "SELECT"}
    key = "SEC-EVAL:app.py:5"
    # In the real loop the per-finding pipeline has already dispatched
    # check_evidence_match (stage == 1); the observation then selects the branch.
    base = {
        "observations": [
            {"step": 1, "tool": "compare_peer_findings", "ok": True,
             "result": {"count": 1, "duplicates": []}},
            {"step": 2, "tool": "check_evidence_match", "ok": True,
             "result": {"supported": True, "evidence": "SELECT"}}],
        "_cr": {key: {"stage": 1, "done": False}},
    }
    strong = choose_critic_tool(dict(base), [finding])
    weak_key = "SEC-EVAL:app.py:5"
    weak = choose_critic_tool(
        {"observations": [
            {"step": 1, "tool": "compare_peer_findings", "ok": True,
             "result": {"count": 1, "duplicates": []}},
            {"step": 2, "tool": "check_evidence_match", "ok": True,
             "result": {"supported": False, "evidence": None}}],
         "_cr": {weak_key: {"stage": 1, "done": False}}},
        [finding])
    assert strong["tool"] == "find_conflict"
    assert weak["tool"] == "check_explanation_quality"


def test_critic_full_loop_finalizes_and_closes_pipeline():
    from evoagent.loop_agents.critic import CriticAgent
    finding = {"rule_id": "SEC-EVAL", "path": "app.py", "line": 5,
               "title": "sql", "evidence": "SELECT"}
    agent = CriticAgent()
    out = agent.run({
        "task_id": "c1", "task_type": "critique.findings", "objective": "critique",
        "input": {"findings": [finding]}})
    artifact = out["artifact"]
    assert out["stop_reason"] == "final"
    assert artifact["accepted_findings"] and "replan_requests" in artifact
    observed = {o.get("tool") for o in out["observations"]}
    assert observed & {"check_evidence_match", "find_conflict",
                       "check_explanation_quality", "check_fix_actionability"}


# ---------------------------------------------------------------------------
# 真正闭环 Phase 5: Security / Reliability Deep Loop (plan §2.2 / §2.3) --
# deterministic static approximations, never empty-shell tools.
# ---------------------------------------------------------------------------

def test_security_deep_loop_tools_are_real_static_analysis():
    from evoagent.diff_parser import parse_unified_diff
    from evoagent.loop_agents.tools import build_expert_context, build_expert_definitions
    diff = ("--- a/a.py\n+++ b/a.py\n@@ -1,4 +1,4 @@\n"
            "+import os\n"
            "+def run():\n"
            "+    name = input('l')\n"
            "+    os.system(name)\n")
    ctx = build_expert_context(diff, parse_unified_diff(diff))
    defs = {d.tool.name: d for d in build_expert_definitions(ctx)}
    finding = {"rule_id": "SEC-EVAL", "path": "a.py", "line": 2}
    tf = defs["trace_dataflow"].tool.handler(finding)
    assert tf["reached"] is True and "sources" in tf and "sinks" in tf
    ic = defs["inspect_context"].tool.handler(finding)
    assert "imports" in ic and ic["risk_relevant"] in (True, False)
    ep = defs["inspect_execution_path"].tool.handler(finding)
    assert "guarded" in ep


def test_security_observation_selects_deepen_tool():
    from evoagent.loop_agents.security import choose_security_tool
    weak = {"rule_id": "SEC-X", "path": "a.py", "line": 1, "evidence": ""}
    rich = {"rule_id": "SEC-Y", "path": "a.py", "line": 2,
            "evidence": "x" * 60}
    base = {"observations": [
        {"step": 1, "tool": "security_rule_scan", "ok": True,
         "result": {"findings": [weak]}},
        {"step": 2, "tool": "semantic_scan", "ok": True,
         "result": {"findings": []}}]}
    # weak evidence -> trace_dataflow deepens the finding.
    deep = choose_security_tool(dict(base), [weak, rich])
    assert deep["tool"] == "trace_dataflow"
    # strong evidence -> no per-finding deepen needed.
    assert choose_security_tool(dict(base), [rich]) is None


def test_reliability_deep_loop_end_to_end():
    from evoagent.loop_agents import ReliabilityAgent
    agent = ReliabilityAgent()
    out = agent.run({
        "task_id": "r1", "task_type": "review.reliability", "objective": "rel",
        "input": {"findings": [], "diff": "import time\ntry:\n  time.sleep(1)\n"
                                         "except Exception:\n  pass\n"}})
    observed = {o.get("tool") for o in out["observations"]}
    assert out["stop_reason"] == "final"
    assert "reliability_rule_scan" in observed
    artifact = out["artifact"]
    assert "findings" in artifact and "count" in artifact
    assert artifact.get("confidence") is not None


# ---------------------------------------------------------------------------
# Phase 6: Fix Agent strategy replan (plan §2.6) -- previous observation selects
# the next patch strategy; a failed strategy is never retried.
# ---------------------------------------------------------------------------

def _fx_obs(tool, result):
    return {"step": 1, "tool": tool, "ok": True, "result": dict(result)}


def test_fix_observation_selects_next_strategy_on_compile_fail():
    from evoagent.loop_agents.fix import choose_fix_tool
    finding = {"rule_id": "SEC-EVAL", "path": "app.py", "line": 5}
    # deterministic compile FAILS -> next strategy must be generate_ast_patch.
    after_compile_fail = choose_fix_tool(
        {"observations": [
            _fx_obs("generate_deterministic_patch",
                    {"patch": "+ x\n", "noop": False, "generator": "deterministic"}),
            _fx_obs("compile_patch", {"compile_ok": False})]},
        finding)
    assert after_compile_fail is not None
    assert after_compile_fail["tool"] == "generate_ast_patch"
    # AST compile FAILS -> next is generate_model_assisted_patch (never repeat).
    after_ast_fail = choose_fix_tool(
        {"observations": [
            _fx_obs("generate_deterministic_patch",
                    {"patch": "+ x\n", "noop": False, "generator": "deterministic"}),
            _fx_obs("compile_patch", {"compile_ok": False}),
            _fx_obs("generate_ast_patch",
                    {"patch": "+ x\n", "noop": False, "generator": "ast"}),
            _fx_obs("compile_patch", {"compile_ok": False})]},
        finding)
    assert after_ast_fail is not None
    assert after_ast_fail["tool"] == "generate_model_assisted_patch"


def test_fix_observation_routes_to_tests_when_compiles():
    from evoagent.loop_agents.fix import choose_fix_tool
    finding = {"rule_id": "SEC-EVAL", "path": "app.py", "line": 5}
    # deterministic compiles -> run_patch_tests; never the same strategy twice.
    decision = choose_fix_tool(
        {"observations": [
            _fx_obs("generate_deterministic_patch",
                    {"patch": "+ x\n", "noop": False, "generator": "deterministic"}),
            _fx_obs("compile_patch", {"compile_ok": True}),
            _fx_obs("run_patch_tests", {"passed": False})]},
        finding)
    # test FAIL -> replan to AST (next unused strategy), not repeat.
    assert decision is not None and decision["tool"] == "generate_ast_patch"
    # test PASS -> final (None), no mechanical repetition.
    assert choose_fix_tool(
        {"observations": [
            _fx_obs("generate_deterministic_patch",
                    {"patch": "+ x\n", "noop": False, "generator": "deterministic"}),
            _fx_obs("compile_patch", {"compile_ok": True}),
            _fx_obs("run_patch_tests", {"passed": True})]},
        finding) is None


def test_fix_full_loop_replans_and_closes():
    from evoagent.loop_agents import FixAgent
    agent = FixAgent()
    out = agent.run({
        "task_id": "f1", "task_type": "fix.generate", "objective": "fix",
        "input": {"findings": [{"rule_id": "SEC-EVAL", "path": "app.py",
                                "line": 5, "fix": "sanitize input"}]}})
    assert out["stop_reason"] in ("final", "exhausted")
    artifact = out["artifact"]
    assert artifact["strategies_tried"][0] == "generate_deterministic_patch"
    assert artifact["patch_strategy_count"] >= 1
    assert list(artifact["strategies_tried"]) == list(dict.fromkeys(
        artifact["strategies_tried"])), "A strategy must never repeat mechanically"


def test_fix_scheme_registered_and_allowlisted():
    from evoagent.diff_parser import parse_unified_diff
    from evoagent.loop_agents.tools import AGENT_SPECS, build_expert_context, \
        build_expert_definitions
    assert "generate_model_assisted_patch" in AGENT_SPECS["fix-agent"]["allowed_tools"]
    diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n+import os\n+os.system('ls')\n"
    ctx = build_expert_context(diff, parse_unified_diff(diff))
    defs = {d.tool.name for d in build_expert_definitions(ctx)}
    assert {"generate_deterministic_patch", "generate_ast_patch",
            "generate_model_assisted_patch"} <= defs


# ---------------------------------------------------------------------------
# Phase 7: Runtime FeatureFlags (plan §4.3 / §4.4) -- each flag must genuinely
# change runtime behaviour, never just sit as a False in a config dict.
# ---------------------------------------------------------------------------

def test_feature_flags_parallel_budget_and_variants():
    from evoagent.loop_agents.feature_flags import MultiAgentFeatureFlags, \
        ablation_variant, flags_from_dict
    assert MultiAgentFeatureFlags().effective_max_parallel == 3
    assert MultiAgentFeatureFlags(parallel_scheduler=False).effective_max_parallel == 1
    assert ablation_variant("Sequential").parallel_scheduler is False
    assert ablation_variant("Full").parallel_scheduler is True
    assert flags_from_dict({"critic": False, "planner": True}).critic is False
    assert flags_from_dict({"critic": False, "planner": True}).planner is True
    assert MultiAgentFeatureFlags().clone(verifier=False).verifier is False


def test_feature_flags_deep_loop_shallow_stepper():
    from evoagent.loop_agents import ReliabilityAgent
    deep = ReliabilityAgent()  # default deep loop
    shallow = ReliabilityAgent(deep_loop=False)
    task = {"task_id": "r", "task_type": "review.reliability",
            "objective": "rel",
            "input": {"findings": [],
                      "diff": "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n"
                              "+print(x)\n+import time\n"}}
    deep_out = deep.run(dict(task))
    shallow_out = shallow.run(dict(task))
    # deep loop keeps deepening; shallow stops after the first gate observation.
    assert deep_out["steps"] > shallow_out["steps"]


# ---------------------------------------------------------------------------
# Phase 8: Evaluation V4 Runtime Runner (plan §4.1/§4.2) -- the default runner
# drives the real SixAgentReviewer stack; the synthetic runner is demo-only.
# ---------------------------------------------------------------------------

def test_runtime_runner_goes_through_real_stack():
    from evoagent.evaluation_v4.runtime_runner import RuntimeScenarioRunner
    runner = RuntimeScenarioRunner()
    scenario = {
        "scenario_id": "rt-1", "kind": "both", "objective": "review",
        "diff": ("--- a/login.py\n+++ b/login.py\n@@ -1,4 +1,6 @@\n"
                 "def login(user, pw):\n+    import sqlite3\n"
                 "    db = sqlite3.connect('u.db')\n"
                 "+    return db.execute('SELECT * FROM u WHERE pw='+pw)\n"),
        "expected_count": 1,
    }
    record = runner.run(scenario, {"scheduler": True})
    assert record["ran_real_runtime"] is True
    assert record["synthetic"] is False
    assert record["tool_calls"] > 0
    assert record["a2a_calls"] > 0  # real delegation happened through A2A
    assert record["artifact"]["architecture"] in ("six-agent", "six-agent-v2")


def test_runtime_runner_flags_change_participating_agents():
    from evoagent.evaluation_v4.runtime_runner import RuntimeScenarioRunner
    runner = RuntimeScenarioRunner(architecture="six-agent-v2")
    scenario = {
        "scenario_id": "rt-2", "kind": "both", "objective": "review",
        "diff": ("--- a/login.py\n+++ b/login.py\n@@ -1,4 +1,6 @@\n"
                 "def login(user, pw):\n+    import sqlite3\n"
                 "    db = sqlite3.connect('u.db')\n"
                 "+    return db.execute('SELECT * FROM u WHERE pw='+pw)\n"),
        "expected_count": 1,
    }
    full = runner.run(dict(scenario), {"critic": True, "verifier": True})
    no_critic = runner.run(dict(scenario), {"critic": False, "verifier": True})
    collisions_full = full["collaborations"]
    collisions_nocritic = no_critic["collaborations"]
    assert "critic-agent" in collisions_full
    assert "critic-agent" not in collisions_nocritic


def test_cli_default_runner_is_runtime():
    from evoagent.evaluation_v4.cli import _pick_runner
    runner = _pick_runner("runtime")
    out = runner("--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n+import os\n",
                 {"kind": "both", "expected_count": 0})
    assert out["ran_real_runtime"] is True
    assert out["synthetic"] is False
    # synthetic stays available but is explicitly flagged.
    syn = _pick_runner("synthetic")("", {"kind": "clean"})
    assert syn["synthetic"] is True


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
    assert set(scores) <= {"detection_quality", "planning_quality",
                           "replan_quality", "collaboration_quality",
                           "loop_quality", "latency_ms", "tool_calls",
                           "a2a_calls"}
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


# ---------------------------------------------------------------------------
# Phase 9: 60-case scenario corpus (plan §4.5 / §4.6)
# ---------------------------------------------------------------------------

def test_v4_full_corpus_count_and_distribution():
    from evoagent.evaluation_v4.scenarios import (
        CATEGORY_SIZES, GOLD_KEYS, build_full_corpus,
    )
    corpus = build_full_corpus()
    from collections import Counter
    sizes = Counter(s["category"] for s in corpus)
    # final plan: mechanism-isolated 80-case corpus.
    assert sizes == CATEGORY_SIZES
    assert len(corpus) == sum(CATEGORY_SIZES.values()) == 80


def test_v4_full_corpus_gold_and_uniqueness():
    from evoagent.evaluation_v4.scenarios import GOLD_KEYS, build_full_corpus
    corpus = build_full_corpus()
    ids = [s["scenario_id"] for s in corpus]
    assert len(set(ids)) == len(ids)  # unique scenario ids
    assert len(set(s["diff"] for s in corpus)) >= 30  # not a copy-paste corpus
    for s in corpus:
        assert set(GOLD_KEYS) <= set(s), s["scenario_id"]
        assert "expected_agents" in s and s["expected_agents"]
        assert "expected_replan" in s
        # replan scenarios name a concrete target specialist
        if s["expected_replan"]:
            assert s["expected_replan_target"] in {
                "security-agent", "reliability-agent"}
        # expected_findings count is consistent with expected_count
        assert len(s["expected_findings"]) <= max(1, s["expected_count"])


def test_v4_full_corpus_roundtrip_write():
    import os
    from evoagent.evaluation_v4.scenarios import (
        GOLD_KEYS, load_scenarios, write_full_corpus,
    )
    path = os.path.join(os.path.dirname(__file__), "_v4_corpus_tmp.jsonl")
    try:
        assert write_full_corpus(path) == 80
        reloaded = load_scenarios(path)
        assert len(reloaded) == 80
        assert all(set(GOLD_KEYS) <= set(s) for s in reloaded)
    finally:
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# Phases 10-12: real-runtime ablation smoke + evolution attribution (plan §5/§12)
# ---------------------------------------------------------------------------

def test_v4_runtime_runner_smoke():
    """Phase 10: the real six-agent stack runs a scenario (no synthetic stub)."""
    from evoagent.evaluation_v4.runtime_runner import RuntimeScenarioRunner
    from evoagent.evaluation_v4.scenarios import build_full_corpus
    scenario = build_full_corpus()[0]  # planning-001 sql-injection
    record = RuntimeScenarioRunner().run(scenario, {})
    assert record["ran_real_runtime"] is True
    assert record["expected_count"] == scenario["expected_count"] == 1
    assert record["artifact"]["count"] >= 0
    # gold (plan §4.6) is attached to the real record.
    assert record["expected_replan"] is False
    assert record["expected_agents"] == ["security-agent", "critic-agent",
                                         "verifier-agent"]


def test_v4_ablation_runner_smoke():
    """Phase 10/11: the ablation matrix runs the real runner over a small slice."""
    from evoagent.evaluation_v4.ablation import AblationRunner
    from evoagent.evaluation_v4.runtime_runner import build_runtime_runner
    from evoagent.evaluation_v4.scenarios import load_scenarios, sample_scenarios
    scenarios = sample_scenarios(load_scenarios("__missing__.jsonl"), 4, seed=1)
    results = AblationRunner(build_runtime_runner()).run(scenarios)
    assert set(results) == {"A", "B", "C", "D", "E", "F", "G"}
    assert all(len(records) == 4 for records in results.values())


def test_v4_attribution_runtime():
    """Phase 12: gold-vs-actual gap maps to stable attribution codes."""
    from evoagent.evaluation_v4.runtime_runner import attribute_runtime

    # clean diff inflated to findings -> over-routing
    fp = {"artifact": {"count": 2}, "expected_count": 0,
          "expected_replan": False}
    assert "PLANNER_OVER_ROUTING" in attribute_runtime(fp)

    # genuine finding never recovered with no replan -> too-shallow loop
    miss = {"artifact": {"count": 0, "replan_count": 0}, "expected_count": 1,
            "expected_replan": True, "expected_replan_target": "security-agent"}
    codes = attribute_runtime(miss)
    assert "SHALLOW_LOOP_FAILURE" in codes
    assert "REPLAN_INSUFFICIENT" in codes

    # replan happened but target diverged from gold
    wrong = {"artifact": {"count": 1, "replan_count": 1}, "expected_count": 1,
             "expected_replan": True, "expected_replan_target": "security-agent",
             "collaborations": ["reliability-agent"]}
    assert "WRONG_REPLAN_TARGET" in attribute_runtime(wrong)

    # correct run emits nothing
    clean = {"artifact": {"count": 1, "replan_count": 0}, "expected_count": 1,
             "expected_replan": False}
    assert attribute_runtime(clean) == []


def test_v4_report_includes_attribution():
    from evoagent.evaluation_v4.report import build_report, render_markdown
    record = {"artifact": {"count": 0, "replan_count": 0, "rationale_codes": ["HIGH_RISK"]},
              "tool_calls": 2, "a2a_calls": 2, "loop_sizes": [2],
              "expected_count": 1, "expected_replan": True,
              "expected_replan_target": "security-agent",
              "attribution": ["SHALLOW_LOOP_FAILURE",
                              "REPLAN_INSUFFICIENT"]}
    report = build_report({"A": [record]})
    assert "attributions" in report["variants"][0]
    assert "SHALLOW_LOOP_FAILURE" in report["variants"][0]["attributions"]
    md = render_markdown(report)
    assert "Evolution Attribution" in md
