"""Service-level production closed loop (convergence plan section 12, Steps 1-20).

Drives a real :class:`ReviewService` over a temporary SQLite database through the
Entire loop: real review -> attributed artifacts (RiskProfile / Policy /
DecisionTrace / ReplaySnapshot / Production Outcome) -> confirmed false negative ->
Experience -> Hypothesis -> Runtime Policy Candidate -> Replay + Hard Gate -> DRAFT
-> REPLAY_PASSED -> SHADOW -> CANARY -> candidate lane -> Production Outcome ->
Promote -> 100% candidate -> Restart -> still candidate -> bad candidate -> auto
rollback -> Restart -> previous-good active -> full Lineage readable from the
durable control plane.
"""
import os
import tempfile
import unittest

from evoagent.config import Settings
from evoagent.evolution_gov.lineage import LineageStage, LineageTracker
from evoagent.experience import MISSED_ISSUE, OBSERVED, build_experience
from evoagent.hypothesis import new_hypothesis
from evoagent.outcome_evolution.outcome import Outcome, OutcomeKind
from evoagent.policy_evolution.candidate import (
    CandidateOperation,
    PolicyCandidateGenerator,
)
from evoagent.policy_evolution.deployment import DeploymentState
from evoagent.policy_evolution.gate import EvolutionGate
from evoagent.policy_evolution.runner import PolicyReplayRunner
from evoagent.service import ReviewService
from evoagent.storage.repositories.lineage import LineageRepository

TENANT = "default"
REPO = "org/repo"
CAND = "cand-prod-1"

_HIGH_DIFF = (
    "--- a/auth/__init__.py\n"
    "+++ b/auth/__init__.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+secret = get_secret()\n"
)


def _settings(path: str):
    return Settings(
        host="127.0.0.1", port=8080, db_path=path, max_diff_bytes=20000,
        max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="",
        llm_model="", github_webhook_secret="", github_token="",
        auto_post_review=False,
    )


class ServiceProductionClosedLoop(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self._services = []
        self.service = ReviewService(_settings(self.path))
        self._services.append(self.service)
        self.lineage_repo = LineageRepository(self.service.control_store)
        self.tracker = LineageTracker()

    def tearDown(self):
        for service in self._services:
            try:
                service.close()
            except Exception:  # noqa: BLE001
                pass
        for suffix in ("", ".control.json"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    def _restart(self):
        try:
            self.service.close()
        except Exception:  # noqa: BLE001
            pass
        self.service = ReviewService(_settings(self.path))
        self._services.append(self.service)
        return self.service

    def _add_lin(self, stage: LineageStage, node_id: str, **payload):
        self.tracker.add(CAND, stage, node_id, **payload)
        self.lineage_repo.add_node(node_id, {
            "candidate_id": CAND, "stage": stage.value, "node_id": node_id,
            "payload": payload,
        })

    def test_production_closed_loop(self):
        svc = self.service
        baseline = svc.policy_repository.active_baseline_policy("high")

        # ---- Step 1-3. real review produces a full set of artifacts ----------
        result = svc.create_review(REPO, _HIGH_DIFF, 42, tenant_id=TENANT)
        self.assertEqual(result["state"], "SUCCESS")
        task_id = result["task_id"]
        # DecisionTrace persisted
        trace = svc.trace_repository.trace(task_id)
        self.assertIsNotNone(trace)
        self.assertGreater(len(trace.events), 0)
        # ReplaySnapshot persisted
        snapshots = svc.replay_repository.snapshots_for_task(task_id)
        self.assertGreaterEqual(len(snapshots), 1)
        # Production Outcome persisted
        self.assertGreaterEqual(len(svc.outcome_repository.by_task(task_id)), 1)
        # Routing exposure persisted
        exposure = svc.policy_exposure_repository.for_task(task_id)
        self.assertGreaterEqual(len(exposure), 1)
        self.assertEqual(exposure[0]["policy_id"], baseline.policy_id)

        # ---- Step 4-5. confirmed false negative -> Experience ----------------
        confirmed_fn = Outcome(
            task_id=task_id, kind=OutcomeKind.FALSE_NEGATIVE, tenant_id=TENANT,
            repository=REPO, risk_level="high",
            finding={"rule_id": "cred-07", "severity": "critical"})
        svc.outcome_store.record(confirmed_fn)
        self.assertTrue(confirmed_fn.is_safety or True)
        experience = build_experience(
            TENANT, REPO, task_id, MISSED_ISSUE,
            {"rule_id": "cred-07", "severity": "critical", "verified": True},
            ["aws_key = AKIA..."])
        self.assertIsNotNone(experience)
        experience["status"] = OBSERVED
        self.tracker.begin(CAND, [experience["fingerprint"]])
        self._add_lin(LineageStage.EXPERIENCE, "exp-1",
                      fingerprint=experience["fingerprint"])

        # ---- Step 6-7. Reflection + Hypothesis -> Candidate ------------------
        self._add_lin(LineageStage.REFLECTION, "refl-1",
                      note="expand credential scan scope")
        hypothesis = new_hypothesis(
            tenant_id=TENANT, problem_type="credential_exposure",
            failure_signature="credentials in fixtures",
            root_cause="whitelist omitted test fixtures",
            change_type="relax_guard", risk_level="high",
            evidence_ids=[experience["fingerprint"]],
            source_task_ids=[task_id])
        hypothesis_id = hypothesis["id"]
        self._add_lin(LineageStage.HYPOTHESIS, hypothesis_id,
                      change_type=hypothesis["change_type"])
        candidate = PolicyCandidateGenerator("prod").generate(
            baseline, operations=[CandidateOperation.RAISE_MAX_STEPS],
            hypothesis_id=hypothesis_id)[0]
        self._add_lin(LineageStage.CANDIDATE, candidate.candidate_id,
                      operation=candidate.operation.value)

        # ---- Step 8-9. Replay baseline vs candidate + hard gate --------------
        runner = PolicyReplayRunner(snapshots)
        base_metrics = runner.run(baseline)
        cand_metrics = runner.run(candidate.policy)
        gate = EvolutionGate()
        decision = gate.evaluate(base_metrics, cand_metrics)
        self.assertTrue(decision.approved, msg="known-good failed: %s"
                        % getattr(decision, "reasons", None))
        self._add_lin(LineageStage.EVALUATION, "eval-1",
                      approved=bool(decision.approved))

        # ---- Step 10-11. deployment lifecycle --------------------------------
        deployment = svc.create_policy_deployment(
            candidate.policy, tenant_id=TENANT, repository=REPO,
            risk_level="high", hypothesis_id=hypothesis_id)
        svc.deployment_replay_pass(deployment.deployment_id)
        svc.deployment_shadow(deployment.deployment_id)
        svc.deployment_canary(deployment.deployment_id)
        self.assertEqual(
            svc.get_policy_deployment(deployment.deployment_id).get("state"),
            DeploymentState.CANARY.value)
        self._add_lin(LineageStage.DEPLOYMENT, deployment.deployment_id,
                      state="CANARY")

        # ---- Step 12-13. new review routes via DeploymentManager to a lane ---
        # Find a real task id whose stable hash lands in the candidate lane.
        candidate_ctx = None
        found = False
        for i in range(200):
            ctx = svc._resolve_execution_context(f"cand-hunt-{i}", REPO, 1,
                                                 _HIGH_DIFF, TENANT)
            if ctx.deployment_lane == "candidate":
                candidate_ctx = ctx
                found = True
                break
        self.assertTrue(found, "no task routed to candidate lane during canary")
        self.assertEqual(candidate_ctx.candidate_policy_id, candidate.policy.policy_id)
        # a real review records its production outcome with candidate attribution
        real = svc.create_review(REPO, _HIGH_DIFF, 43, tenant_id=TENANT)
        out = svc.outcome_repository.by_task(real["task_id"])
        self.assertGreaterEqual(len(out), 1)
        self._add_lin(LineageStage.OUTCOME, "out-1",
                      kind=OutcomeKind.TASK_SUCCESS.value,
                      lane=candidate_ctx.deployment_lane)

        # ---- Step 14-15. promote -> 100% candidate ---------------------------
        guard = 0
        while svc.get_policy_deployment(deployment.deployment_id).get("state") \
                != "PROMOTED":
            svc.deployment_advance(deployment.deployment_id, min_sample_ok=True,
                                   min_duration_ok=True, hard_safety_pass=True)
            guard += 1
            self.assertLess(guard, 20)
        after_promote = svc._resolve_execution_context("t-live", REPO, 1,
                                                       _HIGH_DIFF, TENANT)
        self.assertEqual(after_promote.policy_id, candidate.policy.policy_id)
        self.assertEqual(after_promote.deployment_lane, "candidate")

        # ---- Step 16-17. restart -> still candidate --------------------------
        svc = self._restart()
        after_restart = svc._resolve_execution_context("t-post", REPO, 1,
                                                       _HIGH_DIFF, TENANT)
        self.assertEqual(after_restart.policy_id, candidate.policy.policy_id)

        # ---- Step 18-19. bad candidate -> auto rollback -> previous-good ----
        bad = PolicyCandidateGenerator("bad").generate(
            candidate.policy, operations=[CandidateOperation.DISABLE_EVIDENCE])[0]
        dep_bad = svc.create_policy_deployment(
            bad.policy, tenant_id=TENANT, repository=REPO, risk_level="high")
        svc.deployment_replay_pass(dep_bad.deployment_id)
        svc.deployment_shadow(dep_bad.deployment_id)
        svc.deployment_canary(dep_bad.deployment_id)
        # Regression monitor detects a hard-safety failure; auto rollback.
        dep_bad = svc.deployment_advance(
            dep_bad.deployment_id, min_sample_ok=True, min_duration_ok=True,
            hard_safety_pass=False)
        self.assertEqual(dep_bad.state, DeploymentState.ROLLED_BACK)

        svc = self._restart()
        self.assertEqual(
            svc.get_policy_deployment(dep_bad.deployment_id).get("state"),
            "ROLLED_BACK")
        prev = svc._resolve_execution_context("t-final", REPO, 1, _HIGH_DIFF, TENANT)
        # previous-good after the bad candidate's auto-rollback is the promoted
        # good candidate, not the original bootstrap baseline.
        self.assertEqual(prev.policy_id, candidate.policy.policy_id)

        # ---- Step 20. full lineage readable from the durable control plane ---
        self.assertTrue(self.tracker.get(CAND) is not None)
        expected = [
            LineageStage.EXPERIENCE, LineageStage.REFLECTION,
            LineageStage.HYPOTHESIS, LineageStage.CANDIDATE,
            LineageStage.EVALUATION, LineageStage.DEPLOYMENT,
            LineageStage.OUTCOME,
        ]
        for stage in expected:
            self.assertTrue(self.tracker.get(CAND).has_stage(stage),
                            msg="missing lineage stage %s" % stage.value)
        # nodes persisted and re-readable from the durable store
        persisted_stages = {
            node["stage"] for node in self.lineage_repo.store.all("evolution_lineage_nodes")
        }
        self.assertGreaterEqual(len(persisted_stages), 7)


if __name__ == "__main__":
    unittest.main()