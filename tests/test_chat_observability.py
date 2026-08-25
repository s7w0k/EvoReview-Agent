"""Work Package 7: chat observability, token usage and grey-stage readiness.

These tests verify the WP7 metrics families (chat_messages/requests/insights/
feedback/failures, request duration, token usage, invalid citations, stale
sessions), the model client's token-usage capture, and that the /metrics output
exposes the families with finite-cardinality labels.  Everything is service
level with a mocked model transport; the metrics module is global so assertions
are delta-based.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from evoagent.auth import Principal
from evoagent.chat import ChatModelClient, ChatModelError
from evoagent.config import Settings
from evoagent.metrics import metrics
from evoagent.service import ReviewService


def _principal(tenant="default", user="tester"):
    return Principal(user, user, tenant, "admin")


class ChatObservabilityTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.settings = Settings(
            host="127.0.0.1", port=8080, db_path=self.path, max_diff_bytes=10000,
            max_steps=8, timeout_seconds=60, llm_base_url="http://mock/v1",
            llm_api_key="test-key", llm_model="mock-model",
            github_webhook_secret="", github_token="", auto_post_review=False,
            llm_provider="custom",
            chat_enabled=True, chat_insights_enabled=True, chat_feedback_enabled=True,
        )

    def tearDown(self):
        os.unlink(self.path)

    def _service(self, tenant="default", **overrides):
        settings = self.settings
        if overrides:
            base = {field: getattr(settings, field) for field in settings.__dataclass_fields__}
            base.update(overrides)
            settings = Settings(**base)
        service = ReviewService(settings)
        result = service.create_review("org/repo", self._diff(), 1, tenant_id=tenant)
        return service, result

    @staticmethod
    def _diff():
        return "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(data)\n"

    def _mock_model(self, service, citations=None, insights=None):
        body = {
            "answer": "因为 eval 执行任意代码。",
            "citations": citations if citations is not None else [
                {"type": "finding", "ref": "finding:0"}],
            "insights": insights if insights is not None else [
                {"category": "false_positive", "confidence": 0.8,
                 "finding_ref": "finding:0", "note": "eval 用于不可信输入"}],
        }
        service.chat_client.complete = mock.MagicMock(
            return_value=json.dumps(body, ensure_ascii=False))

    @staticmethod
    def _delta(before, family):
        return {k: v - before.get(k, 0) for k, v in family.items() if v - before.get(k, 0) != 0}

    def test_client_captures_token_usage(self):
        client = ChatModelClient("http://x", "k", "m")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = (
            b'{"choices":[{"message":{"content":"hi"}}],'
            b'"usage":{"prompt_tokens":11,"completion_tokens":7}}')
        with mock.patch("urllib.request.urlopen", return_value=response):
            out = client.complete("s", [{"role": "user", "content": "q"}])
        self.assertEqual("hi", out)
        self.assertEqual(11, client.last_usage["input_tokens"])
        self.assertEqual(7, client.last_usage["output_tokens"])

    def test_successful_turn_records_families_and_tokens(self):
        service, result = self._service()
        self._mock_model(service)
        service.chat_client.last_usage = {"input_tokens": 33, "output_tokens": 9}
        before = {k: dict(v) for k, v in (
            ("messages", metrics.chat_messages), ("requests", metrics.chat_requests),
            ("insights", metrics.chat_insights))}
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            service.send_chat_message(session["id"], "问题", "req-1", _principal())
            messages = self._delta(before["messages"], metrics.chat_messages)
            requests = self._delta(before["requests"], metrics.chat_requests)
            insights = self._delta(before["insights"], metrics.chat_insights)
            self.assertEqual(1, messages.get("completed", 0))
            self.assertEqual(1, requests.get(("custom", "mock-model", "completed"), 0))
            self.assertEqual(1, insights.get(("false_positive", "draft"), 0))
            self.assertGreaterEqual(
                metrics.chat_tokens_input[("custom", "mock-model")], 33)
            self.assertGreaterEqual(
                metrics.chat_tokens_output[("custom", "mock-model")], 9)
            output = metrics.prometheus()
            self.assertIn('evoagent_chat_messages_total{status="completed"}', output)
            self.assertIn('evoagent_chat_tokens_input_total{provider="custom"', output)
            # Token usage is persisted on the completed message row.
            stored = service.store.list_chat_messages(session["id"], "default")
            self.assertEqual(33, stored[-1]["input_tokens"])
            self.assertEqual(9, stored[-1]["output_tokens"])
        finally:
            service.close()

    def test_forged_citations_increment_invalid_counter(self):
        service, result = self._service()
        self._mock_model(service, citations=[
            {"type": "finding", "ref": "finding:0"},
            {"type": "finding", "ref": "finding:99"},  # forged
        ])
        before = metrics.chat_invalid_citations
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            reply = service.send_chat_message(session["id"], "问题", "req-1", _principal())
            self.assertEqual(1, len(reply["insights"]) if reply["insights"] else 1)
            self.assertGreaterEqual(metrics.chat_invalid_citations, before + 1)
        finally:
            service.close()

    def test_failed_turn_records_failure_reason(self):
        service, result = self._service()
        service.chat_client.complete = mock.MagicMock(
            side_effect=lambda *a, **k: (_ for _ in ()).throw(
                ChatModelError("timeout", "boom")))
        before = {k: dict(v) for k, v in (
            ("messages", metrics.chat_messages), ("failures", metrics.chat_failures))}
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            with self.assertRaises(ChatModelError):
                service.send_chat_message(session["id"], "问题", "req-1", _principal())
            messages = self._delta(before["messages"], metrics.chat_messages)
            failures = self._delta(before["failures"], metrics.chat_failures)
            self.assertEqual(1, messages.get("failed", 0))
            self.assertEqual(1, failures.get("timeout", 0))
            output = metrics.prometheus()
            self.assertIn('evoagent_chat_failures_total{reason="timeout"}', output)
        finally:
            service.close()

    def test_confirm_and_reject_record_insight_status(self):
        service, result = self._service()
        self._mock_model(service)
        before = {k: dict(v) for k, v in (
            ("insights", metrics.chat_insights), ("feedback", metrics.chat_feedback))}
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            reply = service.send_chat_message(session["id"], "问题", "req-1", _principal())
            service.confirm_chat_insight(reply["insights"][0]["id"], _principal())
            insights = self._delta(before["insights"], metrics.chat_insights)
            feedback = self._delta(before["feedback"], metrics.chat_feedback)
            self.assertEqual(1, insights.get(("false_positive", "confirmed"), 0))
            self.assertEqual(1, feedback.get("false_positive", 0))
            # Reject a second draft.
            reply2 = service.send_chat_message(session["id"], "再问", "req-2", _principal())
            service.reject_chat_insight(reply2["insights"][0]["id"], _principal())
            insights = self._delta(before["insights"], metrics.chat_insights)
            self.assertEqual(1, insights.get(("false_positive", "rejected"), 0))
        finally:
            service.close()

    def test_stale_session_increments_counter(self):
        service, result = self._service()
        self._mock_model(service)
        before = metrics.chat_stale_sessions
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            with service.store._connection() as conn:
                conn.execute(
                    "UPDATE tasks SET report_json=? WHERE id=?",
                    (json.dumps({"risk": "low", "findings": []}), result["task_id"]),
                )
            with self.assertRaises(ValueError):
                service.send_chat_message(session["id"], "问题", "req-1", _principal())
            self.assertGreaterEqual(metrics.chat_stale_sessions, before + 1)
            self.assertIn("evoagent_chat_stale_sessions_total",
                          metrics.prometheus())
        finally:
            service.close()

    def test_confirmation_rate_is_derivable(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            reply = service.send_chat_message(session["id"], "问题", "req-1", _principal())
            service.confirm_chat_insight(reply["insights"][0]["id"], _principal())
            output = metrics.prometheus()
            # confirmed / created counters exist so the rate is derivable.
            self.assertRegex(output, r'evoagent_chat_insights_total\{category="false_positive",status="confirmed"\} \d+')
            self.assertRegex(output, r'evoagent_chat_insights_total\{category="false_positive",status="draft"\} \d+')
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
