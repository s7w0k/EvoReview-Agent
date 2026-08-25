"""Work Package 10: production observability and operations.

Covers the plan's acceptance items:
- /health, /health/live, /health/ready behave correctly and /health stays
  unchanged;
- graceful shutdown is idempotent and safe under concurrency;
- migrate_db.py --check is read-only/idempotent on an old-schema fixture and
  never makes destructive changes;
- new metric families appear in /metrics output without breaking old keys.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from evoagent.api import ApiHandler
from evoagent.config import Settings
from evoagent.metrics import metrics
from evoagent.observability import AlertManager
from evoagent.service import ReviewService
from evoagent.store import TaskStore


def settings(path, **kwargs):
    values = {
        "host": "127.0.0.1", "port": 8080, "db_path": path, "max_diff_bytes": 10000,
        "max_steps": 8, "timeout_seconds": 10, "llm_base_url": "", "llm_api_key": "",
        "llm_model": "", "github_webhook_secret": "", "github_token": "",
        "auto_post_review": False, "skills_dir": "skills",
    }
    values.update(kwargs)
    return Settings(**values)


class CapturingHandler(ApiHandler):
    service = None
    settings = None

    def __init__(self, path):
        self.path = path
        self.wfile = io.BytesIO()
        self.captured_status = None
        self.captured_body = b""

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


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.service = ReviewService(settings(self.path))
        CapturingHandler.service = self.service
        CapturingHandler.settings = self.service.settings

    def tearDown(self):
        self.service.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _get(self, path):
        handler = CapturingHandler.__new__(CapturingHandler)
        CapturingHandler.__init__(handler, path)
        handler.do_GET()
        return handler.captured_status, handler.captured_body

    def test_health_snapshot_is_unchanged(self):
        status, body = self._get("/health")
        self.assertEqual(200, status)
        self.assertEqual(
            {"status", "reviewer", "runtime", "queue", "llm_provider", "llm_model"},
            set(body.keys()),
        )
        self.assertEqual("ok", body["status"])

    def test_live_endpoint(self):
        status, body = self._get("/health/live")
        self.assertEqual(200, status)
        self.assertEqual({"status": "live"}, body)

    def test_ready_endpoint_reports_dependencies(self):
        status, body = self._get("/health/ready")
        self.assertEqual(200, status)
        self.assertEqual("ready", body["status"])
        self.assertTrue(body["checks"]["database"])
        self.assertTrue(body["checks"]["queue"])

    def test_ready_returns_503_when_dependency_down(self):
        with mock.patch.object(self.service.queue, "ready", return_value=False):
            status, body = self._get("/health/ready")
        self.assertEqual(503, status)
        self.assertEqual("not_ready", body["status"])
        self.assertFalse(body["checks"]["queue"])


class ShutdownTests(unittest.TestCase):
    def test_close_is_idempotent_and_releases_queue(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        service = ReviewService(settings(path))
        try:
            service.close()
            service.close()  # second call must be a no-op
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_close_under_pending_queue_work_is_safe(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        service = ReviewService(settings(path))
        try:
            service.store.create("task-x", "org/a", 1, {}, "default")
            service.store.save_task_payload("task-x", "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(x)\n")
            service.queue.submit({"task_id": "task-x", "tenant_id": "default"})
            service.close()
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_observability_close_is_idempotent(self):
        from evoagent.observability import Observability
        obs = Observability("evoagent-test", "")
        obs.close()
        obs.close()


class MigrateDbTests(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _old_schema_fixture(self):
        """A pre-upgrade database with only the legacy tasks table + data."""
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        import sqlite3
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, state TEXT NOT NULL, "
            "repository TEXT NOT NULL, pull_request INTEGER, input_json TEXT NOT NULL, "
            "report_json TEXT, error TEXT, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tasks VALUES ('t1','SUCCESS','org/a',1,'{}','{}',NULL,'2026-01-01','2026-01-01')"
        )
        conn.commit()
        conn.close()
        return path

    def _run(self, *args):
        script = os.path.join(self.ROOT, "scripts", "migrate_db.py")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, script] + list(args),
            capture_output=True, text=True, env=env, cwd=self.ROOT,
        )

    def test_check_is_read_only_and_reports_missing_schema(self):
        path = self._old_schema_fixture()
        try:
            result = self._run("--db", path, "--check")
            self.assertEqual(1, result.returncode)
            self.assertIn("missing table", result.stdout)
            # Read-only: the tasks row must survive untouched.
            import sqlite3
            conn = sqlite3.connect(path)
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            conn.close()
            self.assertEqual(1, count)
        finally:
            os.unlink(path)

    def test_migrate_is_idempotent_and_non_destructive(self):
        path = self._old_schema_fixture()
        try:
            first = self._run("--db", path)
            self.assertEqual(0, first.returncode)
            self.assertIn("schema OK", first.stdout)
            second = self._run("--db", path)
            self.assertEqual(0, second.returncode)
            self.assertIn("schema OK", second.stdout)
            # Data survives the upgrade.
            import sqlite3
            conn = sqlite3.connect(path)
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            conn.close()
            self.assertEqual(1, count)
        finally:
            os.unlink(path)

    def test_check_after_migration_is_clean(self):
        path = self._old_schema_fixture()
        try:
            self._run("--db", path)
            result = self._run("--db", path, "--check")
            self.assertEqual(0, result.returncode)
            self.assertIn("schema OK", result.stdout)
        finally:
            os.unlink(path)


class MetricsOutputTests(unittest.TestCase):
    def test_new_families_appear_and_old_keys_survive(self):
        # The metrics object is a process-wide singleton; reset it so this
        # test is deterministic regardless of the order it runs in.
        for name in ("counters", "duration_sum", "duration_count", "agent_calls",
                     "agent_failures", "finding_distribution", "rule_fp", "rule_total"):
            getattr(metrics, name).clear()
        metrics.inc("reviews_total", 3)
        metrics.record_agent("planner-agent", 0.42, failed=False)
        metrics.record_agent("critic-agent", 0.1, failed=True)
        metrics.record_finding("tenant-a", "org/repo", "SEC-EVAL", "local")
        metrics.record_rule_feedback("SEC-EVAL", True)
        output = metrics.prometheus()
        # Old keys survive alongside the new labelled families.
        self.assertIn("evoagent_reviews_total 3.0", output)
        self.assertIn('evoagent_agent_calls_total{agent="planner-agent"} 1', output)
        self.assertIn('evoagent_agent_failures_total{agent="critic-agent"} 1', output)
        self.assertIn('evoagent_agent_seconds_count{agent="planner-agent"} 1', output)
        self.assertIn('tenant="tenant-a"', output)
        self.assertIn('rule_id="SEC-EVAL"', output)
        self.assertIn("evoagent_finding_distribution_total", output)
        self.assertIn('evoagent_rule_false_positives_total{rule_id="SEC-EVAL"} 1', output)

    def test_store_ping_and_queue_ready(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            store = TaskStore(path)
            self.assertTrue(store.ping())
            with mock.patch.object(store, "_connect", side_effect=RuntimeError("down")):
                self.assertFalse(store.ping())
        finally:
            os.unlink(path)

    def test_alert_manager_queue_health_alerts(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            store = TaskStore(path)
            fake_queue = type("Q", (), {
                "pending_count": lambda self: 150,
                "dead_letter_count": lambda self: 30,
            })()
            AlertManager(store).evaluate_queue(fake_queue, "tenant-a", max_pending=100, max_dead_letters=20)
            keys = {alert["alert_key"] for alert in store.list_alerts("tenant-a")}
            self.assertIn("queue:backlog", keys)
            self.assertIn("queue:dead-letters", keys)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
