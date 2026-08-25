"""Unit tests for the convergence-plan core wiring (Phase 1-3 building blocks).

Covers the policy codec, the persisted deployment / exposure / outcome
repositories, the manager's ``route`` / ``restore_active_deployments``, the
resolver safety-floor entry point and the execution-context attribution fields.
"""
import os
import tempfile
import unittest
from dataclasses import replace

from evoagent.execution.context import ReviewExecutionContext
from evoagent.policy.codec import policy_from_dict, policy_signature, policy_to_dict
from evoagent.policy.defaults import default_policy
from evoagent.policy.resolver import PolicyResolver
from evoagent.policy.risk import RiskProfile
from evoagent.policy_evolution.deployment import (
    DeploymentState,
    PolicyDeploymentManager,
)
from evoagent.storage.json_store import JSONFileStore
from evoagent.storage.repositories.deployment import DeploymentRepository
from evoagent.storage.repositories.outcome import OutcomeRepository
from evoagent.storage.repositories.policy_exposure import PolicyExposureRepository
from evoagent.storage.repositories.runtime_policy import PersistedRuntimePolicyRepository


class CodecTest(unittest.TestCase):
    def test_round_trip(self):
        p = default_policy("high")
        restored = policy_from_dict(policy_to_dict(p))
        self.assertEqual(restored, p)
        self.assertEqual(restored.policy_version, p.policy_version)

    def test_signature_is_stable(self):
        p = default_policy("medium")
        self.assertEqual(policy_signature(p), policy_signature(p))


class RepositoryPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp("evo")
        self.store = JSONFileStore(os.path.join(self.dir, "control.json"))
        self.deploy_repo = DeploymentRepository(self.store)
        self.exposure_repo = PolicyExposureRepository(self.store)
        self.outcome_repo = OutcomeRepository(self.store)
        self.policy_repo = PersistedRuntimePolicyRepository(self.store)

    def test_policy_survives_restart(self):
        p = default_policy("high")
        row = {"tenant_id": "acme", "policy_id": p.policy_id,
               "risk_level": "high", "version": p.policy_version,
               "parent_version": None, "content": policy_to_dict(p),
               "status": "ACTIVE", "hypothesis_id": ""}
        self.store.save("runtime_policy_versions", p.policy_id, row)
        # reload from a fresh store backed by the same file
        store2 = JSONFileStore(os.path.join(self.dir, "control.json"))
        loaded = PersistedRuntimePolicyRepository(store2).latest(p.policy_id)
        self.assertEqual(loaded["policy_id"], p.policy_id)
        restored = policy_from_dict(loaded["content"])
        self.assertEqual(restored, p)


class ManagerRouteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp("evo")
        self.live = JSONFileStore(os.path.join(self.dir, "live.json"))
        self.deploy_repo = DeploymentRepository(self.live)
        self.exposure_repo = PolicyExposureRepository(self.live)
        self.mgr = PolicyDeploymentManager(
            repo=self.deploy_repo, exposure_repo=self.exposure_repo)

    def test_baseline_when_no_deployment(self):
        baseline = replace(default_policy("high"), policy_id="baseline-high")
        self.mgr.register_policy(baseline)
        decision = self.mgr.route("acme", "acme/prod", "high", "t1")
        self.assertEqual(decision.lane, "baseline")
        self.assertEqual(decision.policy.policy_id, baseline.policy_id)
        self.assertIsNone(decision.deployment_id)

    def test_canary_routes_some_to_candidate_and_records_exposure(self):
        baseline = replace(default_policy("high"), policy_id="baseline-high")
        candidate = replace(default_policy("high"), policy_id="runtime-high-v2",
                            policy_version=2)
        dpl = self.mgr.create(candidate, baseline, tenant_id="acme",
                              repository="acme/prod", risk_level="high")
        self.mgr.replay_pass(dpl.deployment_id)
        self.mgr.shadow(dpl.deployment_id)
        self.mgr.start_canary(dpl.deployment_id)
        lanes = {}
        for i in range(200):
            d = self.mgr.route("acme", "acme/prod", "high", f"task-{i}")
            lanes.setdefault(d.lane, 0)
            lanes[d.lane] += 1
        self.assertGreater(lanes.get("candidate", 0), 0)
        self.assertGreater(lanes.get("baseline", 0), 0)
        self.assertEqual(len(self.mgr.exposure()), 200)
        # persistence across a fresh manager (new store reading the same file)
        revived = PolicyDeploymentManager(
            repo=DeploymentRepository(
                JSONFileStore(os.path.join(self.dir, "live.json"))),
            exposure_repo=PolicyExposureRepository(
                JSONFileStore(os.path.join(self.dir, "live.json"))),
        )
        n = revived.restore_active_deployments(
            policy_loader=lambda pid: {"baseline-high": baseline,
                                       candidate.policy_id: candidate}.get(pid))
        self.assertGreaterEqual(n, 1)
        # stable hash: retried task lands the same lane on the revived manager
        self.assertEqual(
            revived.route("acme", "acme/prod", "high", "task-1").lane,
            self.mgr.route("acme", "acme/prod", "high", "task-1").lane)

    def test_stable_hash_retry_same_lane(self):
        baseline = replace(default_policy("high"), policy_id="baseline-high")
        candidate = replace(default_policy("high"), policy_id="runtime-high-v2",
                            policy_version=2)
        dpl = self.mgr.create(candidate, baseline, tenant_id="acme",
                              repository="acme/prod", risk_level="high")
        self.mgr.replay_pass(dpl.deployment_id)
        self.mgr.shadow(dpl.deployment_id)
        self.mgr.start_canary(dpl.deployment_id)
        lane1 = self.mgr.route("acme", "acme/prod", "high", "taskX").lane
        lane2 = self.mgr.route("acme", "acme/prod", "high", "taskX").lane
        self.assertEqual(lane1, lane2)

    def test_rollback_restores_baseline(self):
        baseline = replace(default_policy("high"), policy_id="baseline-high")
        candidate = replace(default_policy("high"), policy_id="runtime-high-v2",
                            policy_version=2)
        dpl = self.mgr.create(candidate, baseline, tenant_id="acme",
                              repository="acme/prod", risk_level="high")
        self.mgr.replay_pass(dpl.deployment_id)
        self.mgr.shadow(dpl.deployment_id)
        self.mgr.start_canary(dpl.deployment_id)
        self.mgr.rollback(dpl.deployment_id, "hard safety regression")
        self.assertEqual(
            self.mgr.route("acme", "acme/prod", "high", "t-new").policy.policy_id,
            baseline.policy_id)


class ResolverSafetyFloorTest(unittest.TestCase):
    def test_enforce_safety_floor_raises_risk(self):
        resolver = PolicyResolver()
        p = default_policy("low")
        risk = RiskProfile(level="high", score=0.8, reasons=["secrets"])
        enforced = resolver.enforce_safety_floor(p, risk)
        self.assertGreaterEqual(
            {"low": 0, "medium": 1, "high": 2, "critical": 3}[enforced.risk_level], 2)


class ContextAttributionTest(unittest.TestCase):
    def test_new_fields_serialize(self):
        ctx = ReviewExecutionContext(
            task_id="t1", tenant_id="acme", repository="acme/prod",
            execution_policy=default_policy("high"),
            deployment_id="dep-1", deployment_lane="candidate",
            baseline_policy_version=1, candidate_policy_version=2,
            traffic_share=0.1,
        )
        d = ctx.to_dict()
        self.assertEqual(d["deployment_id"], "dep-1")
        self.assertEqual(d["deployment_lane"], "candidate")
        self.assertEqual(d["candidate_policy_version"], 2)
        self.assertEqual(d["traffic_share"], 0.1)


if __name__ == "__main__":
    unittest.main()