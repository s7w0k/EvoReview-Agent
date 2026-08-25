"""Closed-loop WP1: persistent Evolution Controller.

Verifies that enqueue is idempotent, run_job is leased exactly-once, checkpoint/
status transitions are durable, pause/resume/cancel/retry follow the state
machine, and the controller delegates to the existing engines without copying
evaluation logic.
"""
import os
import tempfile
import unittest

from evoagent.config import Settings
from evoagent.evolution_controller import EvolutionController, EvolutionJobError
from evoagent import evolution_state as state
from evoagent.store import TaskStore


def _settings(path, **kwargs):
    values = {
        "host": "127.0.0.1", "port": 8080, "db_path": path, "max_diff_bytes": 10000,
        "max_steps": 8, "timeout_seconds": 10, "llm_base_url": "", "llm_api_key": "",
        "llm_model": "", "github_webhook_secret": "", "github_token": "",
        "auto_post_review": False, "skills_dir": "skills",
    }
    values.update(kwargs)
    return Settings(**values)


class FakeEngine:
    """Deterministic stand-in for EvolutionEngine/SkillEvolutionEngine."""

    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or {
            "decision": "deferred", "run_id": "run-1",
            "version": {"version": 3},
        }
        self.error = error

    def auto_propose(self, skill_name, tenant_id=None):
        self.calls.append((skill_name, tenant_id))
        if self.error:
            raise self.error
        return self.result


class ControllerTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)
        self.settings = _settings(self.path, evolution_controller_enabled=True)
        self.prompt = FakeEngine()
        self.skill = FakeEngine()
        self.controller = EvolutionController(
            self.store, self.settings, self.prompt, self.skill)

    def tearDown(self):
        self.controller.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _enqueue(self, kind="rule_skill", name="evolved-review", trigger="manual", ref=""):
        return self.controller.enqueue("default", kind, name, trigger, ref)

    def test_enqueue_is_idempotent(self):
        first = self._enqueue()
        self.assertTrue(first["created"])
        self.assertEqual(state.JOB_PENDING, first["job"]["status"])
        second = self._enqueue()
        self.assertFalse(second["created"])
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        # Only one pending job row exists for the same key.
        jobs = self.store.list_evolution_jobs("default")
        self.assertEqual(1, len(jobs))

    def test_different_trigger_refs_create_distinct_jobs(self):
        a = self.controller.enqueue("default", "rule_skill", "evolved-review", "event", "fp-a")
        b = self.controller.enqueue("default", "rule_skill", "evolved-review", "event", "fp-b")
        self.assertTrue(a["created"] and b["created"])
        self.assertNotEqual(a["job"]["id"], b["job"]["id"])

    def test_run_job_success_delegates_and_persists(self):
        enqueued = self._enqueue()
        outcome = self.controller.run_job(enqueued["job"]["id"], "default")
        self.assertEqual(state.JOB_COMPLETED, outcome["job"]["status"])
        self.assertEqual(state.STEP_DONE, outcome["job"]["current_step"])
        self.assertEqual("run-1", outcome["job"]["evolution_run_id"])
        self.assertEqual(3, outcome["job"]["candidate_version"])
        self.assertEqual("deferred", outcome["job"]["checkpoint"]["decision"])
        self.assertEqual([("evolved-review", "default")], self.skill.calls)

    def test_run_job_failure_marks_failed(self):
        self.skill.error = RuntimeError("provider down")
        enqueued = self._enqueue()
        with self.assertRaises(RuntimeError):
            self.controller.run_job(enqueued["job"]["id"], "default")
        job = self.store.get_evolution_job(enqueued["job"]["id"], "default")
        self.assertEqual(state.JOB_FAILED, job["status"])
        self.assertIn("provider down", job["error"])

    def test_lease_prevents_double_execution(self):
        enqueued = self._enqueue()
        job_id = enqueued["job"]["id"]
        # First claim wins.
        self.assertTrue(self.store.acquire_evolution_job_lease(
            job_id, "default", "worker-a", "2099-01-01T00:00:00+00:00"))
        # Second claim must fail while status is running.
        self.assertFalse(self.store.acquire_evolution_job_lease(
            job_id, "default", "worker-b", "2099-01-01T00:00:00+00:00"))

    def test_pause_resume_cancel_retry_lifecycle(self):
        enqueued = self._enqueue()
        job_id = enqueued["job"]["id"]
        paused = self.controller.pause(job_id, "default")
        self.assertEqual(state.JOB_PAUSED, paused["job"]["status"])
        resumed = self.controller.resume(job_id, "default")
        self.assertEqual(state.JOB_PENDING, resumed["job"]["status"])
        cancelled = self.controller.cancel(job_id, "default")
        self.assertEqual(state.JOB_CANCELLED, cancelled["job"]["status"])

        # A failed job can be retried (resets to pending, bumps retry_count).
        enqueued2 = self.controller.enqueue(
            "default", "rule_skill", "evolved-review", "event", "fp-retry")
        self.skill.error = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.controller.run_job(enqueued2["job"]["id"], "default")
        self.skill.error = None
        retried = self.controller.retry(enqueued2["job"]["id"], "default")
        self.assertEqual(state.JOB_PENDING, retried["job"]["status"])
        self.assertEqual(1, retried["job"]["retry_count"])

    def test_cannot_run_nonexistent_job(self):
        with self.assertRaises(EvolutionJobError):
            self.controller.run_job("missing", "default")

    def test_retry_respects_budget(self):
        enqueued = self._enqueue()
        job_id = enqueued["job"]["id"]
        self.store.update_evolution_job(
            job_id, "default", status=state.JOB_FAILED,
            retry_count=3, max_retries=3)
        with self.assertRaises(EvolutionJobError):
            self.controller.retry(job_id, "default")

    def test_controller_delegates_prompt_kind(self):
        outcome = self.controller.enqueue(
            "default", "prompt", "llm-review", "manual", "")
        self.controller.run_job(outcome["job"]["id"], "default")
        self.assertEqual([("llm-review", "default")], self.prompt.calls)
        self.assertEqual([], self.skill.calls)


class DisabledControllerTests(unittest.TestCase):
    def test_disabled_controller_is_inert(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            store = TaskStore(path)
            settings = _settings(path, evolution_controller_enabled=False)
            controller = EvolutionController(
                store, settings, FakeEngine(), FakeEngine())
            self.assertFalse(controller.enabled)
            # scan_once is a no-op when disabled (never touches the store).
            self.assertEqual(0, controller.scan_once())
            controller.close()
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
