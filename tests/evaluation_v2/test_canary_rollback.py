"""tests/evaluation_v2/test_canary_rollback.py

Exercise the production deploy lifecycle (plan phase 10 / section 11-12):
a good candidate promotes through the traffic ladder, a known-bad candidate is
auto-rolled-back, and the offline evolution objective agrees (PolicyCanary /
AutoRollback).  Fully deterministic; no network.
"""
import sys
import unittest
from os.path import abspath, dirname

ROOT = dirname(dirname(dirname(abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evoagent.policy.models import ExecutionBudget, ExecutionPolicy  # noqa: E402
from evoagent.policy_evolution.canary import PolicyCanary, CanaryVerdict  # noqa: E402
from evoagent.policy_evolution.candidate import PolicyCandidateGenerator  # noqa: E402
from evoagent.policy_evolution.deployment import (  # noqa: E402
    DeploymentState,
    PolicyDeploymentManager,
)
from evoagent.policy_evolution.objective import EvolutionMetrics  # noqa: E402
from evoagent.policy_evolution.rollback import AutoRollback  # noqa: E402

EVAL_TENANT = "evaluation-v2"


class CanaryRollbackTests(unittest.TestCase):
    def _manager(self):
        baseline = ExecutionPolicy(
            policy_id="baseline-high", policy_version=1, risk_level="high",
            budget=ExecutionBudget(max_steps=8, max_tool_calls=12))
        candidate = PolicyCandidateGenerator().generate(
            baseline, add_agent="security_specialist")[0]
        manager = PolicyDeploymentManager()
        deployment = manager.create(candidate.policy, baseline, tenant_id=EVAL_TENANT,
                                    repository="", risk_level="high")
        return manager, deployment.deployment_id, candidate

    def test_good_candidate_promotes_through_ladder(self):
        manager, did, _ = self._manager()
        manager.replay_pass(did)
        manager.shadow(did)
        manager.start_canary(did)
        guard = 0
        while manager._require(did).state is not DeploymentState.PROMOTED and guard < 20:
            manager.advance_stage(did, min_sample_ok=True, min_duration_ok=True,
                                  hard_safety_pass=True)
            guard += 1
        state = manager._require(did)
        self.assertIs(DeploymentState.PROMOTED, state.state)
        self.assertEqual(1.0, state.traffic_share)

    def test_known_bad_candidate_auto_rolls_back(self):
        manager, did, _ = self._manager()
        manager.replay_pass(did)
        manager.shadow(did)
        manager.start_canary(did)
        rolled = manager.advance_stage(did, min_sample_ok=True, min_duration_ok=True,
                                       hard_safety_pass=False)
        self.assertIs(DeploymentState.ROLLED_BACK, rolled.state)
        self.assertEqual(0.0, rolled.traffic_share)

    def test_canary_verdict_distinguishes_good_from_bad(self):
        baseline = EvolutionMetrics.from_finding_counts(
            tp=20, fp=8, fn=8, high_risk_recall=0.70, critical_misses=2,
            cost=1.0, latency=1.0, reliability_score=0.8, failure_rate=0.0)
        better = EvolutionMetrics.from_finding_counts(
            tp=30, fp=4, fn=3, high_risk_recall=0.95, critical_misses=0,
            cost=1.0, latency=1.0, reliability_score=1.0, failure_rate=0.0)
        bad = EvolutionMetrics.from_finding_counts(
            tp=10, fp=2, fn=20, high_risk_recall=0.40, critical_misses=3,
            cost=1.0, latency=1.0, reliability_score=0.5, failure_rate=0.2)
        good_canary = PolicyCanary()
        good_canary.record("baseline", baseline)
        good_canary.record("candidate", better)
        self.assertIs(CanaryVerdict.PROMOTE, good_canary.decide().verdict)
        bad_canary = PolicyCanary()
        bad_canary.record("baseline", baseline)
        bad_canary.record("candidate", bad)
        self.assertIs(CanaryVerdict.ROLLBACK, bad_canary.decide().verdict)

    def test_auto_rollback_decision_flags_regression(self):
        good = EvolutionMetrics.from_finding_counts(
            tp=30, fp=4, fn=3, high_risk_recall=0.95, critical_misses=0,
            cost=1.0, latency=1.0, reliability_score=1.0, failure_rate=0.0)
        bad = EvolutionMetrics.from_finding_counts(
            tp=10, fp=2, fn=20, high_risk_recall=0.40, critical_misses=3,
            cost=1.0, latency=1.0, reliability_score=0.5, failure_rate=0.2)
        decision = AutoRollback().evaluate(good, bad)
        self.assertTrue(decision.should_rollback)


if __name__ == "__main__":
    unittest.main()