"""Phase 11 acceptance tests: observability / evidence export (plan section 14).

Covers the four evidence endpoints:
  GET /v1/tasks/{id}/decision-trace
  GET /v1/tasks/{id}/replay
  GET /v1/deployments/{id}/metrics
  GET /v1/evolution/{candidate_id}/lineage
"""
import os
import tempfile
import unittest

from evoagent.api import ApiHandler
from evoagent.config import Settings
from evoagent.policy.models import ExecutionPolicy
from evoagent.service import ReviewService


def _settings(path):
    return Settings(
        host="127.0.0.1", port=8080, db_path=path, max_diff_bytes=10000,
        max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="",
        llm_model="", github_webhook_secret="", github_token="",
        auto_post_review=False,
    )


class ObservabilityExportTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.service = ReviewService(_settings(self.path))

    def tearDown(self):
        self.service.queue.close()
        os.unlink(self.path)

    def test_decision_trace_survives_review(self):
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+exec(s)\n"
        result = self.service.create_review("org/repo", diff, 1)
        payload = self.service.task_decision_trace(result["task_id"])
        self.assertEqual(payload["task_id"], result["task_id"])
        self.assertIsInstance(payload.get("events"), list)

    def test_decision_trace_missing_raises(self):
        with self.assertRaises(ValueError):
            self.service.task_decision_trace("00000000-0000-0000-0000-000000000000")

    def test_replay_snapshots_for_task(self):
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+os.system('id')\n"
        result = self.service.create_review("org/repo", diff, 1)
        payload = self.service.task_replay(result["task_id"])
        self.assertEqual(payload["task_id"], result["task_id"])
        self.assertIsInstance(payload["snapshots"], list)

    def test_deployment_metrics_and_lineage(self):
        candidate = ExecutionPolicy(policy_id="cand-obs", policy_version=2)
        deployment = self.service.create_policy_deployment(
            candidate, risk_level="high", repository="repo")
        dep_id = deployment.deployment_id

        self.service.deployment_replay_pass(dep_id)
        self.service.deployment_shadow(dep_id)
        self.service.deployment_canary(dep_id)
        self.service.policy_deployment_manager.resolve_policy(
            "default", "repo", "high", "task-obs-1")
        self.service.deployment_promote(dep_id)

        metrics = self.service.deployment_metrics(dep_id)
        self.assertEqual(metrics["deployment_id"], dep_id)
        self.assertGreaterEqual(metrics["exposure_count"], 1)

        lineage = self.service.evolution_lineage("cand-obs")
        self.assertEqual(lineage["candidate_id"], "cand-obs")
        self.assertTrue(lineage["lineage"])
        self.assertTrue(lineage["events"]["promote"])

    def test_rollback_records_outcome_lineage(self):
        candidate = ExecutionPolicy(policy_id="cand-rb", policy_version=2)
        deployment = self.service.create_policy_deployment(
            candidate, risk_level="high", repository="repo")
        self.service.deployment_replay_pass(deployment.deployment_id)
        self.service.deployment_shadow(deployment.deployment_id)
        self.service.deployment_rollback(deployment.deployment_id, "critical miss")
        lineage = self.service.evolution_lineage("cand-rb")
        self.assertTrue(lineage["events"]["rollback"])
        self.assertEqual(
            lineage["events"]["rollback"][0]["reason"], "critical miss")

    def test_lineage_missing_raises(self):
        with self.assertRaises(ValueError):
            self.service.evolution_lineage("does-not-exist")

    def test_deployment_metrics_missing_raises(self):
        with self.assertRaises(ValueError):
            self.service.deployment_metrics("missing")


class Handler(ApiHandler):
    service = None
    settings = None

    def __init__(self, path):
        import io as _io
        self.path = path
        self.rfile = _io.BytesIO(b"")
        self.headers = {"Content-Length": "0"}
        self.captured_status = None
        self.captured_body = None

    def send_response(self, code, message=None):
        self.captured_status = code

    def send_header(self, *args, **kwargs):
        pass

    def end_headers(self):
        pass

    def _headers(self, status, content_type, length):
        self.captured_status = status

    def _send_json(self, status, value):
        self.captured_status = status
        self.captured_body = value

    def _send_text(self, status, text, content_type="text/plain; charset=utf-8"):
        self.captured_status = status
        self.captured_body = text

    def log_message(self, fmt, *args):
        pass


class ObservabilityHttpTests(unittest.TestCase):
    """Exercise the four evidence endpoints over the real ApiHandler."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.service = ReviewService(_settings(self.path))
        Handler.service = self.service
        Handler.settings = self.service.settings

    def tearDown(self):
        self.service.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _get(self, path):
        handler = Handler.__new__(Handler)
        Handler.__init__(handler, path)
        handler.headers["Authorization"] = "Bearer dev"
        handler.do_GET()
        return handler.captured_status, handler.captured_body

    def test_decision_trace_endpoint(self):
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(x)\n"
        result = self.service.create_review("org/repo", diff, 1)
        status, body = self._get(
            "/v1/tasks/%s/decision-trace" % result["task_id"])
        self.assertEqual(200, status)
        self.assertEqual(result["task_id"], body["task_id"])

    def test_replay_endpoint(self):
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+os.system('id')\n"
        result = self.service.create_review("org/repo", diff, 1)
        status, body = self._get("/v1/tasks/%s/replay" % result["task_id"])
        self.assertEqual(200, status)
        self.assertEqual(result["task_id"], body["task_id"])

    def test_deployment_metrics_endpoint(self):
        candidate = ExecutionPolicy(policy_id="cand-http", policy_version=2)
        deployment = self.service.create_policy_deployment(
            candidate, risk_level="high", repository="repo")
        status, body = self._get(
            "/v1/deployments/%s/metrics" % deployment.deployment_id)
        self.assertEqual(200, status)
        self.assertEqual(deployment.deployment_id, body["deployment_id"])

    def test_evolution_lineage_endpoint_404(self):
        status, _ = self._get("/v1/evolution/ghost/lineage")
        self.assertEqual(404, status)

    def test_unknown_evidence_endpoint_404(self):
        status, _ = self._get("/v1/tasks/00000000-0000-0000-0000-000000000000/replay")
        self.assertEqual(404, status)


if __name__ == "__main__":
    unittest.main()