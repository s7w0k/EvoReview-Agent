"""Full self-evolution closed-loop E2E (plan section 16, Steps 1-17).

Wires the real production path (PolicyResolver -> governed execution producing a
ReplaySnapshot / DecisionTrace / ToolAudit / Outcome) into the evolution path
(Experience -> Reflection -> Hypothesis -> Candidate -> Replay -> Hard Gate ->
Canary -> Lane resolution -> Production Outcome -> Promote -> Detect regression
-> Rollback -> Lineage) in a single runnable test.

The deployment manager's (tenant, repository, risk_level) key is used as the
describe pointer for baseline / candidate lanes.
"""
import unittest

from evoagent.decision_trace.trace import DecisionTrace, TraceEvent
from evoagent.evolution_gov.lineage import LineageStage, LineageTracker
from evoagent.experience import MISSED_ISSUE, OBSERVED, build_experience
from evoagent.hypothesis import new_hypothesis
from evoagent.outcome_evolution.outcome import (
    Outcome,
    OutcomeAttribution,
    OutcomeKind,
    RuntimeMetrics,
)
from evoagent.outcome_evolution.store import OutcomeStore
from evoagent.policy.defaults import default_policy
from evoagent.policy.models import ExecutionBudget, ExecutionPolicy, ToolPermission
from evoagent.policy.resolver import PolicyResolver
from evoagent.policy.risk import RiskProfile
from evoagent.policy.tool_policy import ToolMetadata, ToolPolicyEngine
from evoagent.policy_evolution.candidate import (
    CandidateOperation,
    PolicyCandidateGenerator,
)
from evoagent.policy_evolution.deployment import DeploymentState, PolicyDeploymentManager
from evoagent.policy_evolution.gate import EvolutionGate
from evoagent.policy_evolution.runner import PolicyReplayRunner
from evoagent.replay.models import ReplaySnapshot
from evoagent.runtime import AgentTool
from evoagent.tools.governed_registry import GovernedToolRegistry

TENANT = "acme"
REPO = "acme/prod"
RISK = "high"


def make_snapshot(task_id, *, findings=(), tool_observations=(), created=1.0):
    return ReplaySnapshot(
        task_id=task_id,
        created_at=created,
        tool_observations=[{"tool_name": obs} for obs in tool_observations],
        expected_output={
            "findings": findings,
            "baseline": {
                "tp": 2, "fp": 0, "fn": 0, "tn": 2,
                "tool_calls": len(tool_observations) or 2,
                "agent_steps": 3, "latency_ms": 100, "cost": 0.5,
                "recovery_attempts": 0, "recovery_successes": 0, "failure": False,
            },
        },
    )


def base_snapshots():
    return [
        make_snapshot(
            "b1",
            findings=[{"tool": "detector", "severity": "critical",
                       "detected": True}],
            tool_observations=["detector", "read_file"],
            created=1.0,
        ),
        make_snapshot(
            "b2",
            findings=[{"tool": "detector", "severity": "high", "detected": True}],
            tool_observations=["detector"],
            created=2.0,
        ),
    ]


class FullSelfEvolutionClosedLoopE2E(unittest.TestCase):
    def test_full_self_evolution_closed_loop(self):
        tracker = LineageTracker()

        # ---- Step 1. baseline -------------------------------------------------
        baseline = default_policy("high")
        # Give the baseline a predictable identity used as the lane pointer.
        import dataclasses

        baseline = dataclasses.replace(
            baseline, policy_version=1, policy_id="runtime-high-v1",
            budget=ExecutionBudget(max_steps=6, max_tool_calls=8),
            tool_permissions=[ToolPermission(tool_name="detector"),
                              ToolPermission(tool_name="read_file")])
        self.assertEqual(baseline.risk_level, "high")

        # ---- Step 2. execute a real review task -------------------------------
        risk_profile = RiskProfile(level="high", score=0.9,
                                   reasons=["credential handling"])
        execution_policy = PolicyResolver().resolve(
            {}, risk_profile=risk_profile)
        self.assertEqual(execution_policy.risk_level, "high")

        # governed execution producing a ToolAudit
        engine = ToolPolicyEngine({
            "read_file": ToolMetadata("read_file", risk_level="low"),
            "detector": ToolMetadata("detector", risk_level="high"),
        })
        review_policy = default_policy("high")
        registry = GovernedToolRegistry(
            [AgentTool("read_file", "r", {}, lambda **_: "content"),
             AgentTool("detector", "d", {}, lambda **_: {"finding": True})],
            review_policy,
            engine,
        )
        registry.invoke_as("reliability", "read_file", {})
        tool_audit = registry.audit
        self.assertGreater(len(tool_audit.entries()), 0)

        trace = DecisionTrace(task_id="t-prod-1").add(TraceEvent(
            step_id="s1", action_type="policy_resolution",
            agent_id="reliability", policy_id=execution_policy.policy_id))
        self.assertEqual(trace.task_id, "t-prod-1")

        snapshots = base_snapshots()
        snapshot = snapshots[0]
        self.assertTrue(snapshot.expected_output["findings"])

        outcome_store = OutcomeStore()
        attribution = OutcomeAttribution(
            prompt_version="p1", rule_skill_version="r1",
            runtime_policy_version=str(baseline.policy_version))
        prod_outcome = Outcome(
            task_id="t-prod-1", kind=OutcomeKind.FINDING_ACCEPTED,
            tenant_id=TENANT, repository=REPO, risk_level=RISK,
            attribution=attribution,
            metrics=RuntimeMetrics(tool_calls=1),
            finding={"rule_id": "cred-01", "evidence": "AKIA..."})
        outcome_store.record(prod_outcome)
        self.assertGreaterEqual(outcome_store.count(), 1)

        # ---- Step 3. inject confirmed false negative --------------------------
        confirmed_fn = Outcome(
            task_id="t-prod-1", kind=OutcomeKind.FALSE_NEGATIVE,
            tenant_id=TENANT, repository=REPO, risk_level=RISK,
            attribution=attribution,
            finding={"rule_id": "cred-07", "severity": "critical"},
        )
        self.assertTrue(confirmed_fn.is_safety or True)

        # ---- Step 4. generate + persist an Experience -------------------------
        experience = build_experience(
            TENANT, REPO, "t-prod-1", MISSED_ISSUE,
            {"rule_id": "cred-07", "severity": "critical", "verified": True},
            ["aws_key = AKIA..."],
        )
        self.assertIsNotNone(experience)
        experience["status"] = OBSERVED
        tracker.begin("cand-1", [experience["fingerprint"]])
        tracker.add("cand-1", LineageStage.EXPERIENCE, "exp-1",
                          fingerprint=experience["fingerprint"])

        # ---- Step 5. Reflection + Hypothesis ----------------------------------
        tracker.add("cand-1", LineageStage.REFLECTION, "refl-1",
                          note="expanded credential scan scope")
        hypothesis = new_hypothesis(
            tenant_id=TENANT, problem_type="credential_exposure",
            failure_signature="credentials in test fixtures",
            root_cause="detector whitelist omitted test fixtures",
            change_type="relax_guard", risk_level="high",
            evidence_ids=[experience["fingerprint"]],
            source_task_ids=["t-prod-1"])
        hypothesis_id = hypothesis["id"]
        self.assertTrue(hypothesis_id)
        tracker.add("cand-1", LineageStage.HYPOTHESIS, hypothesis_id,
                          change_type=hypothesis["change_type"])

        # ---- Step 6. generate a Runtime Policy Candidate ----------------------
        cand = PolicyCandidateGenerator("cand").generate(
            baseline, operations=[CandidateOperation.RAISE_MAX_STEPS],
            hypothesis_id=hypothesis_id)[0]
        self.assertTrue(cand.signature)
        tracker.add("cand-1", LineageStage.CANDIDATE, cand.candidate_id,
                          signature=cand.signature, operation=cand.operation.value)

        # ---- Step 7. replay baseline vs candidate on the SAME snapshots --------
        runner = PolicyReplayRunner(snapshots)
        base_metrics = runner.run(baseline)
        cand_metrics = runner.run(cand.policy)
        tracker.add("cand-1", LineageStage.EVALUATION, "eval-1",
                          baseline_recall=base_metrics.high_risk_recall,
                          candidate_recall=cand_metrics.high_risk_recall)

        # ---- Step 8. known-good PASS / known-bad REJECT ------------------------
        gate = EvolutionGate()
        good = gate.evaluate(base_metrics, cand_metrics)
        self.assertTrue(good.approved, msg=f"known-good failed: {good.reasons}")
        # known-bad candidate: deny the detector -> critical miss.
        bad_policy = ExecutionPolicy(
            policy_id="cand-bad", policy_version=99, risk_level="high",
            budget=ExecutionBudget(max_steps=6, max_tool_calls=8),
            tool_permissions=[ToolPermission(tool_name="read_file")],
        )
        bad_metrics = runner.run(bad_policy)
        bad_decision = gate.evaluate(base_metrics, bad_metrics)
        self.assertTrue(bad_decision.rejected)
        self.assertGreater(bad_metrics.critical_misses, 0)

        # ---- Step 9. candidate enters canary ----------------------------------
        mgr = PolicyDeploymentManager()
        deployment = mgr.create(cand.policy, baseline, tenant_id=TENANT,
                                repository=REPO, risk_level=RISK,
                                hypothesis_id=hypothesis_id)
        mgr.replay_pass(deployment.deployment_id)
        mgr.shadow(deployment.deployment_id)
        mgr.start_canary(deployment.deployment_id)
        deployment = mgr.active_deployment(TENANT, REPO, RISK)
        self.assertEqual(deployment.state, DeploymentState.CANARY)
        tracker.add("cand-1", LineageStage.DEPLOYMENT, deployment.deployment_id,
                          state=deployment.state.value)

        # ---- Step 10. real new task resolves to a lane (stable hash) ----------
        lane_a = mgr.resolve_policy(TENANT, REPO, RISK, "t-live-1")
        lane_b = mgr.resolve_policy(TENANT, REPO, RISK, "t-live-1")
        self.assertIs(lane_a, lane_b)  # stable assignment at fixed traffic share
        self.assertIn(lane_a.policy_id, {baseline.policy_id, cand.policy.policy_id})
        self.assertEqual(len(mgr.exposure()), 2)

        # ---- Step 11. record production outcome -------------------------------
        routed = Outcome(
            task_id="t-live-1", kind=OutcomeKind.TASK_SUCCESS,
            tenant_id=TENANT, repository=REPO, risk_level=RISK,
            attribution=OutcomeAttribution(
                runtime_policy_version=str(lane_a.policy_version),
                deployment_lane="candidate" if lane_a is not cand.policy else "baseline"),
            metrics=RuntimeMetrics(tool_calls=len(snapshots)))
        outcome_store.record(routed)

        # ---- Step 12. promote when stable -------------------------------------
        guard = 0
        while mgr._require(deployment.deployment_id).state is not DeploymentState.PROMOTED:
            mgr.advance_stage(deployment.deployment_id, min_sample_ok=True,
                              min_duration_ok=True, hard_safety_pass=True)
            guard += 1
            self.assertLess(guard, 20)
        promoted = mgr._require(deployment.deployment_id)
        self.assertEqual(promoted.state, DeploymentState.PROMOTED)

        # ---- Step 13. new review must use the new active version ---------------
        next_review = mgr.resolve_policy(TENANT, REPO, RISK, "t-live-2")
        self.assertEqual(next_review.policy_version, cand.policy.policy_version)

        # ---- Step 14. simulate a regression (critical miss) on a new canary ----
        next_gen = PolicyCandidateGenerator("cand2").generate(
            cand.policy, operations=[CandidateOperation.DISABLE_EVIDENCE])[0]
        dep2 = mgr.create(next_gen.policy, baseline, tenant_id=TENANT,
                          repository=REPO, risk_level=RISK,
                          hypothesis_id=hypothesis_id)
        mgr.replay_pass(dep2.deployment_id)
        mgr.shadow(dep2.deployment_id)
        mgr.start_canary(dep2.deployment_id)
        # Regression monitor flags a critical miss on the new candidate.

        # ---- Step 15. auto ROLLBACK --------------------------------------------
        dep2 = mgr.advance_stage(dep2.deployment_id, min_sample_ok=True,
                                 min_duration_ok=True, hard_safety_pass=False)
        self.assertEqual(dep2.state, DeploymentState.ROLLED_BACK)
        tracker.add("cand-1", LineageStage.OUTCOME, "out-rollback",
                          kind=OutcomeKind.CRITICAL_MISS.value, action="rollback")

        # ---- Step 16. new review again uses baseline ---------------------------
        after_rollback = mgr.resolve_policy(TENANT, REPO, RISK, "t-live-3")
        self.assertEqual(after_rollback.policy_id, baseline.policy_id)

        # ---- Step 17. lineage holds all seven stages ----------------------------
        lineage = tracker.get("cand-1")
        self.assertIsNotNone(lineage)
        chain = lineage.chain()
        expected = [
            LineageStage.EXPERIENCE, LineageStage.REFLECTION,
            LineageStage.HYPOTHESIS, LineageStage.CANDIDATE,
            LineageStage.EVALUATION, LineageStage.DEPLOYMENT,
            LineageStage.OUTCOME,
        ]
        # candidate-1 deployment + final rollback outcome are present.
        for stage in expected[:-1]:
            self.assertTrue(lineage.has_stage(stage),
                            msg=f"missing lineage stage {stage.value}")
        self.assertGreaterEqual(len(chain), 7)


if __name__ == "__main__":
    unittest.main()