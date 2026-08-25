"""Service-level assignment of policy deployments (convergence plan section 4.6).

Verifies a real :class:`ReviewService` routes live review traffic through the
``PolicyDeploymentManager``:

1. no deployment -> baseline policy;
2. a canary at 5% sends a portion of tasks to the candidate lane;
3. the same task_id retried always lands on the same lane (stable hash);
4. after promote, new tasks use the candidate policy 100%;
5. after rollback, new tasks restore the baseline policy.
"""
import os
import tempfile
import unittest

from evoagent.config import Settings
from evoagent.policy.codec import policy_from_dict
from evoagent.service import ReviewService

TENANT = "default"
REPO = "org/repo"

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


class ServicePolicyDeploymentE2E(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.service = ReviewService(_settings(self.path))

    def tearDown(self):
        try:
            self.service.close()
        except Exception:  # noqa: BLE001
            pass
        for suffix in ("", ".control.json"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    def _ctx(self, task_id):
        return self.service._resolve_execution_context(
            task_id, REPO, 1, _HIGH_DIFF, TENANT)

    def _canary_deployment(self):
        proposed = self.service.propose_policy_candidate(
            tenant_id=TENANT, repository=REPO, risk_level="high")
        candidate = policy_from_dict(proposed["policy"])
        deployment = self.service.create_policy_deployment(
            candidate, tenant_id=TENANT, repository=REPO, risk_level="high")
        self.service.deployment_replay_pass(deployment.deployment_id)
        self.service.deployment_shadow(deployment.deployment_id)
        self.service.deployment_canary(deployment.deployment_id)
        return candidate, deployment

    def test_no_deployment_uses_baseline(self):
        ctx = self._ctx("t-base-1")
        self.assertEqual(ctx.deployment_lane, "baseline")
        self.assertEqual(ctx.policy_id, "baseline-high")
        self.assertIsNone(ctx.deployment_id)

    def test_canary_splits_traffic_and_stable_retry(self):
        candidate, deployment = self._canary_deployment()
        lanes = {}
        for i in range(400):
            lane = self._ctx("task-%d" % i).deployment_lane
            lanes[lane] = lanes.get(lane, 0) + 1
        self.assertGreater(lanes.get("candidate", 0), 0)
        self.assertGreater(lanes.get("baseline", 0), 0)
        # stable hash: the same task_id retried keeps its lane
        self.assertEqual(self._ctx("task-7").deployment_lane,
                         self._ctx("task-7").deployment_lane)
        # every canary-affected task is recorded as an exposure
        rows = self.service.policy_exposure_repository.all_exposures()
        self.assertGreaterEqual(len(rows), 400)

    def test_promote_makes_candidate_fully_active(self):
        candidate, deployment = self._canary_deployment()
        # advance through the whole ladder (5% -> 10% -> 25% -> 50% -> 100%)
        guard = 0
        while self.service.get_policy_deployment(
                deployment.deployment_id).get("state") != "PROMOTED":
            self.service.deployment_advance(
                deployment.deployment_id, min_sample_ok=True,
                min_duration_ok=True, hard_safety_pass=True)
            guard += 1
            self.assertLess(guard, 20)
        ctx = self._ctx("t-new-promoted")
        self.assertEqual(ctx.deployment_lane, "candidate")
        self.assertEqual(ctx.policy_id, candidate.policy_id)

    def test_rollback_restores_baseline(self):
        proposed = self.service.propose_policy_candidate(
            tenant_id=TENANT, repository=REPO, risk_level="high")
        candidate = policy_from_dict(proposed["policy"])
        deployment = self.service.create_policy_deployment(
            candidate, tenant_id=TENANT, repository=REPO, risk_level="high")
        self.service.deployment_replay_pass(deployment.deployment_id)
        self.service.deployment_shadow(deployment.deployment_id)
        self.service.deployment_canary(deployment.deployment_id)
        self.service.deployment_rollback(
            deployment.deployment_id, "hard safety regression")
        ctx = self._ctx("t-new-rolled")
        self.assertEqual(ctx.deployment_lane, "baseline")
        self.assertEqual(ctx.policy_id, "baseline-high")


if __name__ == "__main__":
    unittest.main()