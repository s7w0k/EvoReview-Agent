"""Work Package 3: report-chat service methods and HTTP-facing behaviour.

These tests exercise the service layer (ReviewService.chat_*) with a mocked
model transport.  They verify the dark-switch gating, idempotency, version
constraint (stale sessions), tenant isolation and the retry path.  They never
touch the feedback / evolution chain.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from evoagent.auth import Principal
from evoagent.chat import ChatModelError
from evoagent.config import Settings
from evoagent.service import ReviewService


def _principal(tenant="default", user="tester"):
    return Principal(user, user, tenant, "admin")


class ChatServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.settings = Settings(
            host="127.0.0.1", port=8080, db_path=self.path, max_diff_bytes=10000,
            max_steps=8, timeout_seconds=60, llm_base_url="http://mock/v1",
            llm_api_key="test-key", llm_model="mock-model",
            github_webhook_secret="", github_token="", auto_post_review=False,
            llm_provider="custom",
            chat_enabled=True, chat_insights_enabled=True, chat_feedback_enabled=False,
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

    def _mock_model(self, service, insights=None):
        insights = insights if insights is not None else [
            {"category": "false_positive", "confidence": 0.8,
             "finding_ref": "finding:0", "note": "eval 用于不可信输入"},
        ]
        body = {"answer": "因为 eval 执行任意代码。",
                "citations": [{"type": "finding", "ref": "finding:0"}],
                "insights": insights}
        service.chat_client.complete = mock.MagicMock(
            return_value=json.dumps(body, ensure_ascii=False)
        )

    def test_chat_disabled_rejects_creation(self):
        service, _ = self._service(chat_enabled=False)
        try:
            # service.chat_client is None when chat is disabled, but creation
            # is gated by the dark switch first.
            with self.assertRaises(ValueError):
                service.create_chat_session("any", "t", _principal())
        finally:
            service.close()

    def test_create_session_requires_completed_report(self):
        service, _ = self._service()
        try:
            # A brand new pending task has no report.
            service.store.create("task-empty", "org/repo", 1, {}, "default")
            with self.assertRaises(ValueError):
                service.create_chat_session("task-empty", "t", _principal())
        finally:
            service.close()

    def test_send_message_persists_answer_snapshot_and_draft_insights(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            reply = service.send_chat_message(
                session["id"], "为什么是高风险？", "req-1", _principal())
            self.assertEqual("completed", reply["messages"][0]["status"])
            self.assertIn("eval", reply["messages"][0]["content"])
            # snapshot persisted for the user message.
            snapshot = service.store.get_chat_context_snapshot(
                reply["user_message_id"], "default")
            self.assertTrue(snapshot)
            self.assertEqual(1, len(reply["insights"]))
            self.assertEqual("draft", reply["insights"][0]["status"])
        finally:
            service.close()

    def test_same_client_request_id_does_not_call_model_twice(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            service.send_chat_message(session["id"], "问题 A", "req-dup", _principal())
            service.send_chat_message(session["id"], "问题 A", "req-dup", _principal())
            self.assertEqual(1, service.chat_client.complete.call_count)
            # only one user message row for that request id.
            messages = service.store.list_chat_messages(session["id"], "default")
            self.assertEqual(1, len(messages))
        finally:
            service.close()

    def test_report_change_marks_session_stale(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            # Mutate the stored report so its fingerprint changes.
            with service.store._connection() as conn:
                conn.execute(
                    "UPDATE tasks SET report_json=? WHERE id=?",
                    (json.dumps({"risk": "low", "findings": []}), result["task_id"]),
                )
            with self.assertRaises(ValueError):
                service.send_chat_message(session["id"], "还有问题吗", "req-2", _principal())
            updated = service.store.get_chat_session(session["id"], "default")
            self.assertEqual("stale", updated["status"])
        finally:
            service.close()

    def test_failed_message_retry_keeps_user_input(self):
        service, result = self._service()
        service.chat_client.complete = mock.MagicMock(
            side_effect=lambda *a, **k: (_ for _ in ()).throw(
                ChatModelError("timeout", "boom"))
        )
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            with self.assertRaises(ChatModelError):
                service.send_chat_message(session["id"], "原始问题", "req-retry", _principal())
            messages = service.store.list_chat_messages(session["id"], "default")
            failed = [m for m in messages if m["status"] == "failed"]
            self.assertEqual(1, len(failed))
            self.assertEqual("原始问题", failed[0]["content"])
            # The failing request is retryable and the user's original input is
            # preserved; a fresh request id completes successfully.
            self._mock_model(service)
            reply = service.send_chat_message(
                session["id"], "原始问题", "req-retry2", _principal())
            self.assertEqual("completed", reply["messages"][-1]["status"])
        finally:
            service.close()

    def test_tenant_isolation_on_session_lookup(self):
        service, result = self._service(tenant="tenant-a")
        self._mock_model(service)
        try:
            session = service.create_chat_session(
                result["task_id"], "分析", _principal(tenant="tenant-a"))
            with self.assertRaises(ValueError):
                service.get_chat_session(session["id"], _principal(tenant="tenant-b"))
        finally:
            service.close()

    def test_reject_draft_insight(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            reply = service.send_chat_message(
                session["id"], "请给结论", "req-3", _principal())
            insight_id = reply["insights"][0]["id"]
            outcome = service.reject_chat_insight(insight_id, _principal())
            self.assertTrue(outcome["rejected"])
            self.assertEqual("rejected", outcome["insight"]["status"])
            with self.assertRaises(ValueError):
                service.reject_chat_insight(insight_id, _principal())
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()