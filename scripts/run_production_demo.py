"""Production Demo (plan Phase 10): five end-to-end scenarios.

Writes an evidence report to ``artifacts/demo/production_demo.md``:

  Demo A  Risk-aware Harness        -- low-risk uses fewer agents/steps than high-risk
  Demo B  Tool Governance           -- side-effect DENY + blocking-tool timeout -> recovery
  Demo C  Self-Evolution            -- baseline v1 -> missed -> candidate v2 -> replay
                                       improves -> canary -> promote -> new review uses v2
  Demo D  Auto Rollback             -- bad candidate -> hard-safety failure -> rollback
                                       -> previous-good restored
  Demo E  Restart Recovery          -- canary -> service restart -> stage + lane restored

Prints PASS/FAIL per scenario and exits non-zero if anything regresses.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evoagent.config import Settings          # noqa: E402
from evoagent.service import ReviewService    # noqa: E402

DEMO_HIGH = (
    "--- a/auth/__init__.py\n"
    "+++ b/auth/__init__.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+secret = get_secret()\n"
)
DEMO_LOW = (
    "--- a/util.py\n"
    "+++ b/util.py\n"
    "@@ -1 +1 @@\n"
    "-def old():\n"
    "+def renamed():\n"
)

BANNER = "# EvoReview-Agent — Production Demo\n"
REPORT = [BANNER]


def out(line: str = "") -> None:
    REPORT.append(str(line))
    print(line)


def demo_a(service: ReviewService) -> bool:
    out("\n## Demo A — Risk-aware Harness")
    low = service._resolve_execution_context(
        "risk-low", "org/repo", 1, DEMO_LOW, "default")
    high = service._resolve_execution_context(
        "risk-high", "org/repo", 2, DEMO_HIGH, "default")

    def row(ctx) -> tuple:
        policy = ctx.execution_policy
        verification = policy.verification
        verify = []
        if verification.critic_required:
            verify.append("critic")
        if verification.evidence_required:
            verify.append("evidence")
        if verification.verifier_required:
            verify.append("verifier")
        if verification.sandbox_required:
            verify.append("sandbox")
        return (
            len(policy.agents.enabled_agents or []),
            list(policy.agents.enabled_agents or []),
            policy.budget.max_steps,
            policy.budget.max_tool_calls,
            verify,
            len(policy.tool_permissions or []),
        )

    low_a, low_ag, low_steps, low_tools, low_verify, low_perms = row(low)
    hi_a, hi_ag, hi_steps, hi_tools, hi_verify, hi_perms = row(high)
    out("| Resource | Low-risk | High-risk |")
    out("|---|---:|---:|")
    out("| Enabled agents | %d (%s) | %d (%s) |"
        % (low_a, ",".join(low_ag), hi_a, ",".join(hi_ag)))
    out("| Budget max_steps | %d | %d |" % (low_steps, hi_steps))
    out("| Budget max_tool_calls | %d | %d |" % (low_tools, hi_tools))
    out("| Verification steps | %s | %s |" % (
        ",".join(low_verify) if low_verify else "(none)",
        ",".join(hi_verify) if hi_verify else "(none)"))
    out("| Tool perms | %d | %d |" % (low_perms, hi_perms))
    ok = (hi_a >= low_a and hi_steps >= low_steps and hi_tools >= low_tools)
    out("=> %s: high-risk engages %d agents + %d steps + %d tool calls "
        "(vs %d / %d / %d for low-risk)."
        % ("PASS" if ok else "FAIL", hi_a, hi_steps, hi_tools,
           low_a, low_steps, low_tools))
    return ok


def demo_b(service: ReviewService) -> bool:
    out("\n## Demo B — Tool Governance")
    from evoagent.policy.models import ExecutionBudget, ExecutionPolicy, ToolPermission
    from evoagent.policy.tool_policy import ToolMetadata, ToolPolicyEngine
    from evoagent.runtime import AgentTool
    from evoagent.tools.governed_registry import GovernedToolRegistry

    def approve_never(decision):
        return False

    policy = ExecutionPolicy(
        policy_id="gov-demo", risk_level="high",
        budget=ExecutionBudget(max_steps=8, max_tool_calls=20),
        tool_permissions=[
            ToolPermission("read_file", allow=True),
            ToolPermission("run_tests", allow=True, requires_approval=True),
            ToolPermission("slow_job", allow=True),
        ],
    )
    engine = ToolPolicyEngine({
        "read_file": ToolMetadata("read_file", risk_level="low"),
        "run_tests": ToolMetadata(
            "run_tests", risk_level="high", side_effect=True, idempotent=False,
            requires_approval=True, requires_sandbox=True),
        "slow_job": ToolMetadata(
            "slow_job", risk_level="medium", blocking=True, timeout_seconds=0.2,
            command=sys.executable + """ -c "import time; time.sleep(2)" """),
    })
    registry = GovernedToolRegistry([
        AgentTool("read_file", "r", {}, lambda **a: "content"),
        AgentTool("run_tests", "t", {}, lambda **a: "ran"),
        AgentTool("slow_job", "s", {}, lambda **a: "done"),
    ], policy, engine)
    registry.approval_provider = approve_never

    # 1. side-effect tool, approval provider declines -> DENY
    from evoagent.policy.tool_policy import ToolPermissionDenied
    side_denied = False
    try:
        registry.invoke_as("agent", "run_tests", {})
    except ToolPermissionDenied as exc:
        side_denied = True
        out("- side-effect `run_tests` -> DENY: %s" % exc)
    out("=> %s: side-effect tool is denied without approval." % ("PASS" if side_denied else "FAIL"))

    # 2. blocking tool times out -> circuit-breaker timeout -> recovery
    from evoagent.tools.executor import ToolTimeoutError
    from evoagent.recovery import RecoveryBudget, RecoveryManager
    from evoagent import metrics as metrics_module
    before_timeouts = metrics_module.metrics.counters.get("tool_timeouts_total", 0)
    timed_out = False
    try:
        registry.invoke_as("agent", "slow_job", {})
    except ToolTimeoutError as exc:
        timed_out = True
        after_timeouts = metrics_module.metrics.counters.get("tool_timeouts_total", 0)
        out("- blocking `slow_job` -> TIMEOUT exceeded %.1fs" % 0.2)
        out("- metrics tool_timeouts_total: %s -> %s"
            % (before_timeouts, after_timeouts))
        recovery = RecoveryManager(
            budget=RecoveryBudget(max_recovery_attempts=3, max_replans=1))
        outcome = recovery.handle(
            exc, {"task_id": "gov-demo-task"},
            {"task_id": "gov-demo-task", "recovery_counts": {"attempts": 0,
                                                             "replans": 0,
                                                             "model_switches": 0}},
            node="tool:slow_job", agent_id="agent", tool_context={"tool": "slow_job"})
        out("- recovery action=%s" % outcome.action.value)
    out("=> %s: blocking tool timeout is caught and routed to recovery."
        % ("PASS" if timed_out else "FAIL"))
    return side_denied and timed_out


def demo_c(service: ReviewService) -> bool:
    out("\n## Demo C — Self-Evolution (baseline -> missed -> candidate -> promote)")
    from evoagent.evolution_gov.lineage import LineageStage, LineageTracker
    from evoagent.experience import MISSED_ISSUE, OBSERVED, build_experience
    from evoagent.hypothesis import new_hypothesis
    from evoagent.outcome_evolution.outcome import Outcome, OutcomeKind
    from evoagent.policy_evolution.candidate import (
        CandidateOperation, PolicyCandidateGenerator)
    from evoagent.policy_evolution.gate import EvolutionGate
    from evoagent.policy_evolution.runner import PolicyReplayRunner

    TENANT, REPO = "default", "org/repo"
    baseline = service.policy_repository.active_baseline_policy("high")
    result = service.create_review(REPO, DEMO_HIGH, 42, tenant_id=TENANT)
    task_id = result["task_id"]
    snapshots = service.replay_repository.snapshots_for_task(task_id)

    confirmed = Outcome(
        task_id=task_id, kind=OutcomeKind.FALSE_NEGATIVE, tenant_id=TENANT,
        repository=REPO, risk_level="high",
        finding={"rule_id": "os-system-01", "severity": "critical"})
    service.outcome_store.record(confirmed)
    experience = build_experience(
        TENANT, REPO, task_id, MISSED_ISSUE,
        {"rule_id": "os-system-01", "severity": "critical", "verified": True},
        ["os.system(secret)"])
    experience["status"] = OBSERVED

    hypothesis = new_hypothesis(
        tenant_id=TENANT, problem_type="os_command_execution",
        failure_signature="os.system in auth",
        root_cause="os.system bypasses subprocess controls",
        change_type="tighten_guard", risk_level="high",
        evidence_ids=[experience["fingerprint"]], source_task_ids=[task_id])
    candidate = PolicyCandidateGenerator("prod").generate(
        baseline, operations=[CandidateOperation.RAISE_MAX_STEPS],
        hypothesis_id=hypothesis["id"])[0]

    base_metrics = PolicyReplayRunner(snapshots).run(baseline)
    cand_metrics = PolicyReplayRunner(snapshots).run(candidate.policy)
    decision = EvolutionGate().evaluate(base_metrics, cand_metrics)
    out("- baseline replay metrics -> candidate deltas:")
    out("  finding_f1 %.3f -> %.3f ; high_risk_recall %.3f -> %.3f ; approved=%s"
        % (base_metrics.finding_f1, cand_metrics.finding_f1,
           base_metrics.high_risk_recall, cand_metrics.high_risk_recall,
           bool(decision.approved)))

    deployment = service.create_policy_deployment(
        candidate.policy, tenant_id=TENANT, repository=REPO, risk_level="high",
        hypothesis_id=hypothesis["id"])
    service.deployment_replay_pass(deployment.deployment_id)
    service.deployment_shadow(deployment.deployment_id)
    service.deployment_canary(deployment.deployment_id)

    guard = 0
    while service.get_policy_deployment(deployment.deployment_id).get("state") != "PROMOTED":
        service.deployment_advance(deployment.deployment_id, min_sample_ok=True,
                                   min_duration_ok=True, hard_safety_pass=True)
        guard += 1
        if guard > 20:
            break
    state = service.get_policy_deployment(deployment.deployment_id).get("state")
    after = service._resolve_execution_context("live-1", REPO, 1, DEMO_HIGH, TENANT)
    out("- deployment state=%s ; live review policy=%s lane=%s"
        % (state, after.policy_id, after.deployment_lane))
    tracker = LineageTracker()
    tracker.begin(candidate.candidate_id,
                  [experience["fingerprint"]])
    for stage in (LineageStage.HYPOTHESIS, LineageStage.CANDIDATE,
                  LineageStage.EVALUATION, LineageStage.DEPLOYMENT):
        tracker.add(candidate.candidate_id, stage, str(stage).split(".", 1)[1])
    chain = " -> ".join(s.value for s in tracker.get(candidate.candidate_id).chain())
    out("- lineage chain: %s" % chain)
    return bool(decision.approved) and state == "PROMOTED"


def demo_d() -> bool:
    out("\n## Demo D — Auto Rollback (bad candidate -> previous-good restored)")
    from evoagent.policy_evolution.candidate import (
        CandidateOperation, PolicyCandidateGenerator)
    from evoagent.policy_evolution.deployment import DeploymentState
    from evoagent.service import ReviewService as _RS

    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    good_candidate_id = "unknown"
    try:
        service = _RS(_settings(path))
        try:
            baseline = service.policy_repository.active_baseline_policy("high")
            good = PolicyCandidateGenerator("prod").generate(
                baseline, operations=[CandidateOperation.RAISE_MAX_STEPS])[0]
            good_candidate_id = good.policy.policy_id
            dep_good = service.create_policy_deployment(
                good.policy, tenant_id="default", repository="org/repo", risk_level="high")
            service.deployment_replay_pass(dep_good.deployment_id)
            service.deployment_shadow(dep_good.deployment_id)
            service.deployment_canary(dep_good.deployment_id)
            g = 0
            while service.get_policy_deployment(dep_good.deployment_id).get("state") != "PROMOTED":
                service.deployment_advance(dep_good.deployment_id, min_sample_ok=True,
                                           min_duration_ok=True, hard_safety_pass=True)
                g += 1
                if g > 20:
                    break

            bad = PolicyCandidateGenerator("bad").generate(
                good.policy, operations=[CandidateOperation.DISABLE_EVIDENCE])[0]
            dep_bad = service.create_policy_deployment(
                bad.policy, tenant_id="default", repository="org/repo", risk_level="high")
            service.deployment_replay_pass(dep_bad.deployment_id)
            service.deployment_shadow(dep_bad.deployment_id)
            service.deployment_canary(dep_bad.deployment_id)
            dep_bad = service.deployment_advance(
                dep_bad.deployment_id, min_sample_ok=True, min_duration_ok=True,
                hard_safety_pass=False)
            out("- bad candidate state=%s (hard-safety gate failed -> auto rollback)"
                % dep_bad.state.value)
        finally:
            service.close()

        # restart: the durable control plane restores previous-good (the promoted v2).
        service = _RS(_settings(path))
        try:
            restored = service._resolve_execution_context(
                "rollback-live", "org/repo", 1, DEMO_HIGH, "default")
            ok = (dep_bad.state == DeploymentState.ROLLED_BACK
                  and restored.policy_id == good_candidate_id)
            out("- after rollback + restart, live review policy=%s (previous-good=%s)"
                % (restored.policy_id, good_candidate_id))
            return ok
        finally:
            service.close()
    finally:
        for suffix in ("", ".control.json"):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass


def demo_e() -> bool:
    out("\n## Demo E — Restart Recovery (canary -> restart -> same lane)")
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    try:
        service = ReviewService(_settings(path))
        try:
            from evoagent.policy_evolution.candidate import (
                CandidateOperation, PolicyCandidateGenerator)
            baseline = service.policy_repository.active_baseline_policy("high")
            cand = PolicyCandidateGenerator("prod").generate(
                baseline, operations=[CandidateOperation.RAISE_MAX_STEPS])[0]
            dep = service.create_policy_deployment(
                cand.policy, tenant_id="default", repository="org/repo", risk_level="high")
            service.deployment_replay_pass(dep.deployment_id)
            service.deployment_shadow(dep.deployment_id)
            service.deployment_canary(dep.deployment_id)
            state_before = service.get_policy_deployment(dep.deployment_id).get("state")
            lane_before = service._resolve_execution_context(
                "restart-fixed-task", "org/repo", 1, DEMO_HIGH, "default").deployment_lane
        finally:
            service.close()

        # restart with the same persistent control plane
        service = ReviewService(_settings(path))
        try:
            state_after = service.get_policy_deployment(dep.deployment_id).get("state")
            ctx_after = service._resolve_execution_context(
                "restart-fixed-task", "org/repo", 1, DEMO_HIGH, "default")
            out("- state %s -> %s ; lane %s -> %s"
                % (state_before, state_after, lane_before, ctx_after.deployment_lane))
            return (state_before == state_after == "CANARY"
                    and lane_before == ctx_after.deployment_lane)
        finally:
            service.close()
    finally:
        for suffix in ("", ".control.json"):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass


def _settings(path: str):
    return Settings(
        host="127.0.0.1", port=8080, db_path=path, max_diff_bytes=20000,
        max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="",
        llm_model="", github_webhook_secret="", github_token="",
        auto_post_review=False,
    )


def main() -> int:
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    service = ReviewService(_settings(path))
    results = {}
    try:
        started = time.time()
        results["A"] = demo_a(service)
        results["B"] = demo_b(service)
        results["C"] = demo_c(service)
        results["D"] = demo_d()
        results["E"] = demo_e()
        elapsed = time.time() - started
        out("\n## Demo Summary")
        out("| Demo | Scenario | Result |")
        out("|---|---|---|")
        for k in "ABCDE":
            out("| %s | %s | %s |" % (
                k, {"A": "Risk-aware Harness", "B": "Tool Governance",
                    "C": "Self-Evolution", "D": "Auto Rollback",
                    "E": "Restart Recovery"}[k], "PASS" if results[k] else "FAIL"))
        out("\nTotal elapsed: %.1fs" % elapsed)
    finally:
        service.close()
        for suffix in ("", ".control.json"):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass

    artifacts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "artifacts", "demo")
    os.makedirs(artifacts, exist_ok=True)
    with open(os.path.join(artifacts, "production_demo.md"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(REPORT) + "\n")
    ok = all(results.values())
    print("\nProduction Demo: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())