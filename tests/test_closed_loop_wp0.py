"""Work Package 0 (closed-loop evolution): freeze the compatibility contract.

Covers the plan's acceptance items:
- the new master switches default to false/manual/always and preserve the
  current manual path (no auto-run, no auto-deploy, no auto-activation);
- unknown switch values fail fast at startup;
- the /v1/evolution/*, /v1/skill-evolution/*, /v1/deployments/* and
  /v1/evaluation/cases API response shapes are unchanged (top-level key
  snapshots);
- migrate_db.py --check reports the planned closed-loop tables/columns as
  missing *without* failing or performing a destructive migration.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

from evoagent.api import ApiHandler
from evoagent.config import Settings
from evoagent.service import ReviewService


def _settings(path, **kwargs):
    values = {
        "host": "127.0.0.1", "port": 8080, "db_path": path, "max_diff_bytes": 10000,
        "max_steps": 8, "timeout_seconds": 10, "llm_base_url": "", "llm_api_key": "",
        "llm_model": "", "github_webhook_secret": "", "github_token": "",
        "auto_post_review": False, "skills_dir": "skills",
    }
    values.update(kwargs)
    return Settings(**values)


class ConfigDefaultsTests(unittest.TestCase):
    def test_defaults_preserve_manual_path(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            s = _settings(path)
            self.assertFalse(s.evolution_controller_enabled)
            self.assertEqual("manual", s.evolution_trigger_mode)
            self.assertEqual("always", s.evolution_approval_policy)
            self.assertFalse(s.evolution_production_profile)
            self.assertEqual(1, s.evolution_max_concurrent_jobs)
            self.assertEqual(3600, s.evolution_job_timeout_seconds)
            self.assertEqual(3, s.evolution_job_max_retries)
            self.assertEqual(60, s.evolution_lease_seconds)
        finally:
            os.unlink(path)

    def test_from_env_defaults(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            # No EVOAGENT_EVOLUTION_* set: from_env uses the safe defaults.
            old = {k: os.environ.pop(k, None) for k in list(os.environ)
                   if k.startswith("EVOAGENT_EVOLUTION_")}
            try:
                s = Settings.from_env()
            finally:
                os.environ.update({k: v for k, v in old.items() if v is not None})
            self.assertFalse(s.evolution_controller_enabled)
            self.assertEqual("manual", s.evolution_trigger_mode)
            self.assertEqual("always", s.evolution_approval_policy)
        finally:
            os.unlink(path)


class ConfigValidationTests(unittest.TestCase):
    def _rejects(self, **kwargs):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            s = _settings(path, **kwargs)
            with self.assertRaises(ValueError):
                s.validate_evolution()
        finally:
            os.unlink(path)

    def test_unknown_trigger_mode_fails(self):
        self._rejects(evolution_trigger_mode="auto-magic")

    def test_unknown_approval_policy_fails(self):
        self._rejects(evolution_approval_policy="sometimes")

    def test_zero_concurrent_jobs_fails(self):
        self._rejects(evolution_max_concurrent_jobs=0)

    def test_zero_job_timeout_fails(self):
        self._rejects(evolution_job_timeout_seconds=0)

    def test_negative_retries_fail(self):
        self._rejects(evolution_job_max_retries=-1)

    def test_zero_lease_seconds_fails(self):
        self._rejects(evolution_lease_seconds=0)

    def test_valid_values_pass(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            s = _settings(
                path,
                evolution_trigger_mode="event",
                evolution_approval_policy="high-risk",
                evolution_max_concurrent_jobs=4,
                evolution_job_timeout_seconds=7200,
                evolution_job_max_retries=5,
                evolution_lease_seconds=120,
            )
            s.validate_evolution()  # must not raise
        finally:
            os.unlink(path)


class _CapturingHandler(ApiHandler):
    service = None
    settings = None

    def __init__(self, path):
        self.path = path
        self.wfile = io.BytesIO()
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


class ApiSnapshotTests(unittest.TestCase):
    """Freeze the top-level response keys of the evolution API surface.

    The closed-loop controller must not change the existing HTTP contract; any
    future field is append-only.
    """

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.service = ReviewService(_settings(self.path))
        _CapturingHandler.service = self.service
        _CapturingHandler.settings = self.service.settings

    def tearDown(self):
        self.service.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _get(self, path):
        handler = _CapturingHandler.__new__(_CapturingHandler)
        _CapturingHandler.__init__(handler, path)
        handler.do_GET()
        return handler.captured_status, handler.captured_body

    def test_evolution_status_shape(self):
        status, body = self._get("/v1/evolution/status")
        self.assertEqual(200, status)
        expected = {
            "model_configured", "validation_cases", "holdout_cases",
            "minimum_cases", "minimum_holdout_cases", "maximum_cases_per_run",
            "minimum_improvement", "maximum_metric_regression",
            "validation_dataset_fingerprint", "holdout_dataset_fingerprint",
            "ready", "provider", "model",
        }
        self.assertEqual(expected, set(body.keys()))

    def test_evolution_runs_shape(self):
        status, body = self._get("/v1/evolution/runs")
        self.assertEqual(200, status)
        self.assertEqual({"runs"}, set(body.keys()))
        self.assertIsInstance(body["runs"], list)

    def test_skill_evolution_status_shape(self):
        status, body = self._get("/v1/skill-evolution/status")
        self.assertEqual(200, status)
        expected = {
            "tenant_id", "skill_name", "active_version",
            "active_artifact_sha256", "validation_cases", "holdout_cases",
            "minimum_cases", "minimum_holdout_cases", "minimum_improvement",
            "maximum_metric_regression", "ready",
        }
        self.assertEqual(expected, set(body.keys()))

    def test_skill_evolution_runs_shape(self):
        status, body = self._get("/v1/skill-evolution/runs")
        self.assertEqual(200, status)
        self.assertEqual({"runs"}, set(body.keys()))
        self.assertIsInstance(body["runs"], list)

    def test_deployments_shape(self):
        status, body = self._get("/api/deployments/llm-review")
        self.assertEqual(200, status)
        self.assertEqual({"deployment"}, set(body.keys()))

    def test_evaluation_cases_shape(self):
        status, body = self._get("/v1/evaluation/cases")
        self.assertEqual(200, status)
        self.assertEqual({"cases"}, set(body.keys()))
        self.assertIsInstance(body["cases"], list)

    def test_holdout_cases_are_not_exposed(self):
        status, body = self._get("/v1/evaluation/cases?split=holdout")
        self.assertEqual(403, status)
        self.assertEqual({"error"}, set(body.keys()))


class MigrateDbPlannedSchemaTests(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _run(self, *args):
        script = os.path.join(self.ROOT, "scripts", "migrate_db.py")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, script] + list(args),
            capture_output=True, text=True, env=env, cwd=self.ROOT,
        )

    def test_check_reports_planned_tables_without_failing(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            # Build the current full schema (idempotent), then inspect.
            result = self._run("--db", path)
            self.assertEqual(0, result.returncode)
            # Planned closed-loop tables (WP2+) are reported as missing but
            # --check still succeeds because the required schema is complete.
            check = self._run("--db", path, "--check")
            self.assertEqual(0, check.returncode)
            self.assertIn("planned table (not created yet)", check.stdout)
            self.assertIn("usage_events", check.stdout)
            self.assertIn("planned column (not added yet)", check.stdout)
            self.assertIn("schema OK: required schema present", check.stdout)
        finally:
            os.unlink(path)

    def test_required_schema_still_fails_when_incomplete(self):
        # A legacy DB missing required tables must still fail --check.
        import sqlite3
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
            conn.commit()
            conn.close()
            check = self._run("--db", path, "--check")
            self.assertEqual(1, check.returncode)
            self.assertIn("missing table", check.stdout)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
