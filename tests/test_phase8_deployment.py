"""Phase 8 acceptance tests: canary router + deployment manager.

Covers:
  * resolve_policy over the full deployment lifecycle (12.1)
  * stable lane assignment (hash(task_id + deployment_id)) (12.2)
  * deployment state machine DRAFT->...->PROMOTED / ROLLED_BACK (12.3)
  * staged canary rollout 5%->10%->25%->50%->100% with gates (12.4)
  * exposure logging for attribution (12.5)
  * real rollback: candidate disabled, baseline restored, new tasks = baseline (12.6)
"""
import unittest

from evoagent.policy.models import ExecutionPolicy
from evoagent.policy_evolution.deployment import (
    DeploymentState,
    PolicyDeploymentManager,
)


def policy(policy_id, version=1):
    return ExecutionPolicy(policy_id=policy_id, policy_version=version)


class ResolvePolicyTest(unittest.TestCase):
    def setUp(self):
        self.manager = PolicyDeploymentManager()
        self.baseline = policy("base", 1)
        self.candidate = policy("cand", 2)
        self.deployment = self.manager.create(
            self.candidate, self.baseline, tenant_id="t1",
            repository="repo", risk_level="high", hypothesis_id="h1")

    def test_draft_resolves_baseline(self):
        got = self.manager.resolve_policy("t1", "repo", "high", "task-1")
        self.assertEqual(got.policy_id, "base")

    def test_canary_resolves_by_lane_and_logs_exposure(self):
        self.manager.replay_pass(self.deployment.deployment_id)
        self.manager.shadow(self.deployment.deployment_id)
        self.manager.start_canary(self.deployment.deployment_id)

        got1 = self.manager.resolve_policy("t1", "repo", "high", "task-1")
        got2 = self.manager.resolve_policy("t1", "repo", "high", "task-1")
        # Stable lane -> a retry of the same task stays on the same policy.
        self.assertEqual(got1.policy_id, got2.policy_id)
        records = self.manager.exposure()
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record.lane,
                             "candidate" if got1.policy_id == "cand" else "baseline")
            self.assertEqual(record.baseline_version, 1)
            self.assertEqual(record.candidate_version, 2)

    def test_promoted_resolves_candidate(self):
        self.manager.replay_pass(self.deployment.deployment_id)
        self.manager.shadow(self.deployment.deployment_id)
        self.manager.start_canary(self.deployment.deployment_id)
        self.manager.promote(self.deployment.deployment_id)
        got = self.manager.resolve_policy("t1", "repo", "high", "task-9")
        self.assertEqual(got.policy_id, "cand")


class StableLaneTest(unittest.TestCase):
    def test_same_task_same_lane_many_retries(self):
        manager = PolicyDeploymentManager()
        deployment = manager.create(policy("cand"), policy("base"),
                                    tenant_id="t", repository="r", risk_level="medium")
        manager.replay_pass(deployment.deployment_id)
        manager.shadow(deployment.deployment_id)
        manager.start_canary(deployment.deployment_id)

        lanes = {manager.resolve_policy("t", "r", "medium", "fixed-42").policy_id
                 for _ in range(200)}
        self.assertEqual(len(lanes), 1)  # every retry lands on one lane


class RolloutTest(unittest.TestCase):
    def test_ladder_15_to_100_percent(self):
        manager = PolicyDeploymentManager(min_sample=2, min_duration_seconds=0)
        deployment = manager.create(policy("cand"), policy("base"),
                                    tenant_id="t", repository="r", risk_level="low")
        manager.replay_pass(deployment.deployment_id)
        manager.shadow(deployment.deployment_id)
        manager.start_canary(deployment.deployment_id)
        self.assertEqual(deployment.traffic_share, 0.05)

        steps = 0
        while deployment.state is DeploymentState.CANARY and steps < 10:
            manager.advance_stage(
                deployment.deployment_id,
                min_sample_ok=True, min_duration_ok=True, hard_safety_pass=True)
            steps += 1
        self.assertIs(deployment.state, DeploymentState.PROMOTED)
        self.assertEqual(deployment.traffic_share, 1.0)

    def test_hard_safety_failure_rolls_back_not_advance(self):
        manager = PolicyDeploymentManager(min_sample=2, min_duration_seconds=0)
        deployment = manager.create(policy("cand"), policy("base"),
                                    tenant_id="t", repository="r", risk_level="low")
        manager.replay_pass(deployment.deployment_id)
        manager.shadow(deployment.deployment_id)
        manager.start_canary(deployment.deployment_id)

        advanced = manager.advance_stage(
            deployment.deployment_id,
            min_sample_ok=True, min_duration_ok=True, hard_safety_pass=False)
        self.assertIs(advanced.state, DeploymentState.ROLLED_BACK)
        self.assertEqual(advanced.traffic_share, 0.0)

    def test_stage_holds_without_sample_or_duration(self):
        manager = PolicyDeploymentManager()
        deployment = manager.create(policy("cand"), policy("base"),
                                    tenant_id="t", repository="r", risk_level="low")
        manager.replay_pass(deployment.deployment_id)
        manager.shadow(deployment.deployment_id)
        manager.start_canary(deployment.deployment_id)
        held = manager.advance_stage(
            deployment.deployment_id, min_sample_ok=False,
            min_duration_ok=False, hard_safety_pass=True)
        self.assertIs(held.state, DeploymentState.CANARY)
        self.assertEqual(held.traffic_share, 0.05)


class RealRollbackTest(unittest.TestCase):
    def test_rollback_restores_baseline_for_new_tasks(self):
        manager = PolicyDeploymentManager()
        deployment = manager.create(policy("cand"), policy("base"),
                                    tenant_id="t", repository="r", risk_level="high",
                                    hypothesis_id="h")
        manager.replay_pass(deployment.deployment_id)
        manager.shadow(deployment.deployment_id)
        manager.start_canary(deployment.deployment_id)
        manager.rollback(deployment.deployment_id, reason="regression detected")

        # New tasks must go straight back to baseline.
        got = manager.resolve_policy("t", "r", "high", "new-task")
        self.assertEqual(got.policy_id, "base")
        deployment = manager.active_deployment("t", "r", "high")
        self.assertIs(deployment.state, DeploymentState.ROLLED_BACK)


if __name__ == "__main__":
    unittest.main()