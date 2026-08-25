"""Closed-loop WP1: crash recovery for the persistent Evolution Controller.

Verifies that a job interrupted mid-flight (lease expiry) is recovered from its
checkpoint, that exhausted retry budgets fail the job, and that duplicate
leases cannot be granted to two workers.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from evoagent.config import Settings
from evoagent.evolution_controller import EvolutionController
from evoagent import evolution_state as state
from evoagent.store import TaskStore, utc_now


def _settings(path, **kwargs):
    values = {
        "host": "127.0.0.1", "port": 8080, "db_path": path, "max_diff_bytes": 10000,
        "max_steps": 8, "timeout_seconds": 10, "llm_base_url": "", "llm_api_key": "",
        "llm_model": "", "github_webhook_secret": "", "github_token": "",
        "auto_post_review": False, "skills_dir": "skills",
        "evolution_controller_enabled": True,
    }
    values.update(kwargs)
    return Settings(**values)


class FakeEngine:
    def __init__(self, result=None):
        self.result = result or {"decision": "deferred", "run_id": "r", "version": None}
        self.calls = []

    def auto_propose(self, skill_name, tenant_id=None):
        self.calls.append((skill_name, tenant_id))
        return self.result


def _expired_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)
        self.settings = _settings(self.path)
        self.controller = EvolutionController(
            self.store, self.settings, FakeEngine(), FakeEngine())

    def tearDown(self):
        self.controller.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _create_running_with_expired_lease(self, retry_count=0, max_retries=3):
        job_id = "job-recover"
        self.store.create_evolution_job(
            job_id, "default", None, "rule_skill", "evolved-review",
            "manual", "", "recover-key", {"max_retries": max_retries},
            max_retries=max_retries,
        )
        # Simulate a crash after claiming the lease: running + expired lease.
        self.store.acquire_evolution_job_lease(
            job_id, "default", "dead-worker", _expired_iso())
        self.store.update_evolution_job_checkpoint(
            job_id, "default", state.STEP_EVALUATING, {"step": state.STEP_EVALUATING})
        if retry_count:
            self.store.update_evolution_job(job_id, "default", retry_count=retry_count)
        return job_id

    def test_expired_lease_is_recovered_to_pending(self):
        job_id = self._create_running_with_expired_lease()
        recovered = self.controller.recover_expired()
        self.assertEqual(1, recovered)
        job = self.store.get_evolution_job(job_id, "default")
        self.assertEqual(state.JOB_PENDING, job["status"])
        self.assertEqual(1, job["retry_count"])
        self.assertIsNone(job["lease_owner"])
        self.assertIn("lease expiry", job["error"])

    def test_exhausted_retry_budget_fails_job(self):
        job_id = self._create_running_with_expired_lease(
            retry_count=3, max_retries=3)
        self.controller.recover_expired()
        job = self.store.get_evolution_job(job_id, "default")
        self.assertEqual(state.JOB_FAILED, job["status"])
        self.assertIn("exhausted", job["error"])

    def test_recovered_job_can_be_rerun(self):
        job_id = self._create_running_with_expired_lease()
        self.controller.recover_expired()
        # A fresh controller (new worker identity) takes over the pending job.
        fresh = EvolutionController(
            self.store, self.settings, FakeEngine(), FakeEngine())
        try:
            outcome = fresh.run_job(job_id, "default")
            self.assertEqual(state.JOB_COMPLETED, outcome["job"]["status"])
        finally:
            fresh.close()

    def test_checkpoint_preserved_across_recovery(self):
        job_id = self._create_running_with_expired_lease()
        before = self.store.get_evolution_job(job_id, "default")
        self.assertEqual(state.STEP_EVALUATING, before["checkpoint"]["step"])
        self.controller.recover_expired()
        after = self.store.get_evolution_job(job_id, "default")
        # Checkpoint payload survives the recovery reset.
        self.assertEqual(state.STEP_EVALUATING, after["checkpoint"]["step"])

    def test_two_workers_cannot_claim_same_job(self):
        job_id = "job-race"
        self.store.create_evolution_job(
            job_id, "default", None, "rule_skill", "evolved-review",
            "manual", "", "race-key", {"max_retries": 3}, max_retries=3)
        first = self.store.acquire_evolution_job_lease(
            job_id, "default", "w1", "2099-01-01T00:00:00+00:00")
        second = self.store.acquire_evolution_job_lease(
            job_id, "default", "w2", "2099-01-01T00:00:00+00:00")
        self.assertTrue(first)
        self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
