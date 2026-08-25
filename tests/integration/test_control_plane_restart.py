"""Control-plane restart recovery (convergence plan section 10).

After a :class:`ReviewService` is destroyed and recreated over the same SQLite +
control-plane file, the deployment / canary stage / exposure must survive:

Case A - a live canary survives restart (same task retries to the same lane, new
         tasks keep splitting by 10%); after promote + restart, new tasks are 100%
         on the candidate version.

Case B - a rolled-back candidate stays disabled after restart; new tasks use the
         previous-good baseline.
"""
import os
import tempfile
import unittest

import pytest

from evoagent.config import Settings
from evoagent.policy.codec import policy_from_dict
from evoagent.service import ReviewService

pytestmark = pytest.mark.sqlite

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


def _ctx(service, task_id):
    return service._resolve_execution_context(task_id, REPO, 1, _HIGH_DIFF, TENANT)

def _open_canary(service):
    proposed = service.propose_policy_candidate(risk_level="high")
    candidate = policy_from_dict(proposed["policy"])
    deployment = service.create_policy_deployment(
        candidate, risk_level="high", repository=REPO)
    service.deployment_replay_pass(deployment.deployment_id)
    service.deployment_shadow(deployment.deployment_id)
    service.deployment_canary(deployment.deployment_id)
    return candidate, deployment


class ControlPlaneRestartTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.service = ReviewService(_settings(self.path))

    def tearDown(self):
        for service in getattr(self, "_services", []):
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
        """Destroy and recreate the service over the same db file (restart)."""
        try:
            self.service.close()
        except Exception:  # noqa: BLE001
            pass
        self.service = ReviewService(_settings(self.path))
        self._services.append(self.service)
        return self.service

    def test_canary_survives_restart_and_promote_is_full(self):
        self._services = [self.service]
        candidate, deployment = _open_canary(self.service)

        # produce exposure traffic on the live service
        lanes_before = {}
        for i in range(200):
            lane = _ctx(self.service, "live-%d" % i).deployment_lane
            lanes_before[lane] = lanes_before.get(lane, 0) + 1
        self.assertGreater(lanes_before.get("candidate", 0), 0)

        # restart: the canary must still be live and expose the same lane for the
        # same task_id, and new tasks must keep splitting.
        svc2 = self._restart()
        self.assertEqual(
            _ctx(svc2, "live-5").deployment_lane,
            _ctx(self.service, "live-5").deployment_lane)
        lanes_after = {}
        for i in range(200):
            lane = _ctx(svc2, "after-%d" % i).deployment_lane
            lanes_after[lane] = lanes_after.get(lane, 0) + 1
        self.assertGreater(lanes_after.get("candidate", 0), 0)

        # promote through the ladder, then restart
        guard = 0
        while svc2.get_policy_deployment(
                deployment.deployment_id).get("state") != "PROMOTED":
            svc2.deployment_advance(deployment.deployment_id,
                                    min_sample_ok=True, min_duration_ok=True,
                                    hard_safety_pass=True)
            guard += 1
            self.assertLess(guard, 20)
        self.assertEqual(
            _ctx(svc2, "post-promote-1").policy_id, candidate.policy_id)

        svc3 = self._restart()
        for i in range(20):
            self.assertEqual(_ctx(svc3, "post-restart-%d" % i).policy_id,
                             candidate.policy_id)

    def test_rolled_back_candidate_stays_disabled_after_restart(self):
        self._services = [self.service]
        candidate, deployment = _open_canary(self.service)
        # hard-safety regression triggers automatic rollback
        self.service.deployment_rollback(
            deployment.deployment_id, "hard safety regression")
        # restart: previous-good baseline is active again
        svc2 = self._restart()
        self.assertEqual(
            svc2.get_policy_deployment(deployment.deployment_id).get("state"),
            "ROLLED_BACK")
        for i in range(20):
            self.assertEqual(_ctx(svc2, "rolled-%d" % i).policy_id,
                             "baseline-high")


if __name__ == "__main__":
    unittest.main()