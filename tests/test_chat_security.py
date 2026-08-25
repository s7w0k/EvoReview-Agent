"""Work Package 6: chat security, privacy and reliability hardening.

These tests exercise input/output hardening (citation/insight caps, JSON depth,
secret masking, message truncation), budget & rate limiting (round budget and
per-session concurrency), crash recovery (pending messages), retention/archive
lifecycle and the guarantee that chat failures never affect the core review
pipeline.  Everything is service-level with a mocked model transport.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from evoagent.auth import Principal
from evoagent.chat import ChatBusyError, ChatModelError, decode_model_output
from evoagent.config import Settings
from evoagent.experience import mask_secrets
from evoagent.service import ReviewService


def _principal(tenant="default", user="tester"):
    return Principal(user, user, tenant, "admin")


class ChatSecurityTests(unittest.TestCase):
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

    def _mock_model(self, service, body=None):
        body = body if body is not None else {
            "answer": "因为 eval 执行任意代码。",
            "citations": [{"type": "finding", "ref": "finding:0"}],
            "insights": [{"category": "false_positive", "confidence": 0.8,
                          "finding_ref": "finding:0", "note": "eval 用于不可信输入"}],
        }
        service.chat_client.complete = mock.MagicMock(
            return_value=json.dumps(body, ensure_ascii=False))

    # ---- 6.1 input / output security ----

    def test_citation_count_is_capped(self):
        references = [{"type": "finding", "ref": "finding:%d" % i} for i in range(30)]
        raw = [{"type": "finding", "ref": "finding:%d" % i} for i in range(30)]
        from evoagent.chat import validate_citations
        capped = validate_citations(raw, references, max_citations=5)
        self.assertEqual(5, len(capped))

    def test_insight_count_is_capped(self):
        findings = [{"rule_id": "SEC-EVAL", "path": "a.py", "line": 1}]
        from evoagent.chat import validate_insights
        raw = [{"category": "false_positive", "confidence": 0.8,
                "finding_ref": "finding:0", "note": "n%d" % i} for i in range(30)]
        capped = validate_insights(raw, findings, max_insights=3)
        self.assertEqual(3, len(capped))

    def test_json_depth_limit_rejected(self):
        deep = 1
        for _ in range(30):
            deep = {"a": deep}
        with self.assertRaises(ChatModelError) as ctx:
            decode_model_output(json.dumps(deep), [], [], True)
        self.assertEqual("invalid_output", ctx.exception.reason)

    def test_insight_note_is_masked_for_secrets(self):
        self.assertIn("<REDACTED>", mask_secrets("api_key = sk-1234567890abcdef"))
        self.assertIn("<REDACTED>", mask_secrets("AKIA0123456789ABCDEF"))
        service, result = self._service()
        self._mock_model(service, body={
            "answer": "注意密码", "citations": [],
            "insights": [{"category": "false_positive", "confidence": 0.9,
                          "finding_ref": "finding:0",
                          "note": "password = hunter2hunter2"}],
        })
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            reply = service.send_chat_message(session["id"], "问题", "req-1", _principal())
            self.assertIn("<REDACTED>", reply["insights"][0]["note"])
            self.assertNotIn("hunter2hunter2", reply["insights"][0]["note"])
        finally:
            service.close()

    def test_message_content_is_truncated_to_budget(self):
        service, result = self._service(chat_max_message_chars=24)
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            long_text = "长" * 100
            service.send_chat_message(session["id"], long_text, "req-1", _principal())
            # The context handed to the model carries the truncated question.
            messages = service.chat_client.complete.call_args.args[1]
            question_block = messages[-1]["content"]
            self.assertIn("长" * 24, question_block)
            self.assertNotIn("长" * 25, question_block)
        finally:
            service.close()

    # ---- 6.2 budget and rate limiting ----

    def test_round_budget_rejects_when_exhausted(self):
        service, result = self._service(chat_max_rounds=1)
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            service.send_chat_message(session["id"], "第一轮", "req-1", _principal())
            with self.assertRaises(ValueError) as ctx:
                service.send_chat_message(session["id"], "第二轮", "req-2", _principal())
            self.assertIn("round limit", str(ctx.exception))
        finally:
            service.close()

    def test_concurrent_request_for_same_session_rejected(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            # Simulate an in-flight request for the same session.
            service._chat_inflight.add(session["id"])
            with self.assertRaises(ChatBusyError):
                service.send_chat_message(session["id"], "问题", "req-1", _principal())
            # Guard released -> the request now succeeds.
            service._chat_inflight.discard(session["id"])
            reply = service.send_chat_message(session["id"], "问题", "req-2", _principal())
            self.assertEqual("completed", reply["messages"][-1]["status"])
        finally:
            service.close()

    # ---- 6.3 retention and lifecycle ----

    def test_archive_session_keeps_audit(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            archived = service.archive_chat_session(session["id"], _principal())
            self.assertEqual("archived", archived["status"])
            # Archiving twice is idempotent.
            again = service.archive_chat_session(session["id"], _principal())
            self.assertEqual("archived", again["status"])
            # The session (audit source) still exists.
            self.assertTrue(service.store.get_chat_session(session["id"], "default"))
        finally:
            service.close()

    def test_retention_purge_keeps_sessions_with_confirmed_feedback(self):
        service, result = self._service(chat_retention_days=10)
        self._mock_model(service)
        try:
            old = "2020-01-01T00:00:00+00:00"
            # Session A: plain Q&A, no feedback -> body eligible for removal.
            session_a = service.create_chat_session(result["task_id"], "A", _principal())
            service.send_chat_message(session_a["id"], "问题A", "req-a", _principal())
            with service.store._connection() as conn:
                conn.execute(
                    "UPDATE chat_messages SET created_at=? WHERE session_id=?",
                    (old, session_a["id"]))
            # Session B: confirmed feedback -> message body must be kept.
            session_b = service.create_chat_session(result["task_id"], "B", _principal())
            reply_b = service.send_chat_message(session_b["id"], "问题B", "req-b", _principal())
            service.confirm_chat_insight(reply_b["insights"][0]["id"], _principal())
            with service.store._connection() as conn:
                conn.execute(
                    "UPDATE chat_messages SET created_at=? WHERE session_id=?",
                    (old, session_b["id"]))
            outcome = service.purge_chat_history(_principal())
            self.assertGreaterEqual(outcome["purged"], 1)
            self.assertEqual([], service.store.list_chat_messages(session_a["id"], "default"))
            self.assertTrue(service.store.list_chat_messages(session_b["id"], "default"))
            # Audit rows survive: sessions and insights remain.
            self.assertTrue(service.store.get_chat_session(session_a["id"], "default"))
        finally:
            service.close()

    def test_retention_disabled_by_default(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            service.send_chat_message(session["id"], "问题", "req-1", _principal())
            outcome = service.purge_chat_history(_principal())
            self.assertEqual(0, outcome["purged"])
            self.assertTrue(service.store.list_chat_messages(session["id"], "default"))
        finally:
            service.close()

    # ---- 6.4 crash recovery ----

    def test_startup_recovery_fails_pending_messages(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            service.store.append_chat_message(
                "default", session["id"], "user", "中断的问题", [],
                client_request_id="req-crash", status="pending")
            service.close()
            fresh = ReviewService(self.settings)
            try:
                pending = [m for m in fresh.store.list_chat_messages(session["id"], "default")
                           if m["client_request_id"] == "req-crash"]
                self.assertEqual(1, len(pending))
                self.assertEqual("failed", pending[0]["status"])
                self.assertEqual("中断的问题", pending[0]["content"])
            finally:
                fresh.close()
        finally:
            service.close()

    # ---- chat failure must not affect the core review pipeline ----

    def test_chat_failure_does_not_break_core_review(self):
        service, result = self._service()
        service.chat_client.complete = mock.MagicMock(
            side_effect=lambda *a, **k: (_ for _ in ()).throw(
                ChatModelError("timeout", "boom")))
        try:
            session = service.create_chat_session(result["task_id"], "分析", _principal())
            with self.assertRaises(ChatModelError):
                service.send_chat_message(session["id"], "问题", "req-1", _principal())
            # A brand new review still completes normally.
            second = service.create_review("org/repo", self._diff(), 2, tenant_id="default")
            task = service.store.get(second["task_id"], "default")
            self.assertEqual("SUCCESS", task["state"])
            self.assertTrue(task.get("report"))
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
