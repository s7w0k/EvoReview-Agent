"""Work Package 5: candidate confirmation and controlled sedimentation.

These tests exercise the service layer (ReviewService.edit_chat_insight /
confirm_chat_insight) with a mocked model transport.  They verify that only
*confirmed* chat insights write into the existing feedback chain, that the
write is exactly-once (idempotent via failure_cases.source_key), that
category-level validation blocks illegal candidates (e.g. a missed_issue whose
location is not on a diff added line), that stale reports block confirmation,
and that crash reconciliation restores confirming insights to a consistent
state.  They never bypass the evolution gates.
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


class ChatConfirmTests(unittest.TestCase):
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

    def _session_with_draft(self, service, result):
        session = service.create_chat_session(result["task_id"], "分析", _principal())
        reply = service.send_chat_message(
            session["id"], "为什么是高风险？", "req-1", _principal())
        return session, reply

    @staticmethod
    def _feedback_memory_count(service):
        memories = service.store.list_agent_memories(
            "default", "org/repo", ["semantic"], 500)
        return len([m for m in memories if m.get("kind") == "review_feedback"])

    def test_plain_qa_draft_and_rejected_do_not_write_feedback(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session, reply = self._session_with_draft(service, result)
            insight_id = reply["insights"][0]["id"]
            self.assertEqual("draft", reply["insights"][0]["status"])
            # Reject the draft; nothing may enter the feedback chain.
            service.reject_chat_insight(insight_id, _principal())
            cases = service.store.list_task_failure_cases(result["task_id"], "default")
            self.assertEqual(0, len(cases))
            self.assertEqual([], service.store.list_experiences("default"))
            self.assertEqual(0, self._feedback_memory_count(service))
        finally:
            service.close()

    def test_confirm_writes_feedback_exactly_once_and_is_idempotent(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            _, reply = self._session_with_draft(service, result)
            insight_id = reply["insights"][0]["id"]
            first = service.confirm_chat_insight(insight_id, _principal())
            self.assertTrue(first["confirmed"])
            self.assertIsNotNone(first["insight"]["feedback_case_id"])
            cases = service.store.list_task_failure_cases(result["task_id"], "default")
            self.assertEqual(1, len(cases))
            self.assertEqual("chat_insight:" + insight_id, cases[0]["source_key"])
            payload = cases[0]["payload"]
            self.assertEqual(insight_id, payload["source_metadata"]["insight_id"])
            self.assertTrue(payload["source_metadata"]["report_fingerprint"])
            # Memory is written exactly once by the shared feedback path.
            self.assertEqual(1, self._feedback_memory_count(service))
            # Double confirm / network retry returns the stored result and
            # never inserts a second failure case.
            second = service.confirm_chat_insight(insight_id, _principal())
            self.assertTrue(second["confirmed"])
            self.assertEqual(first["insight"]["feedback_case_id"],
                             second["insight"]["feedback_case_id"])
            cases = service.store.list_task_failure_cases(result["task_id"], "default")
            self.assertEqual(1, len(cases))
            self.assertEqual(1, self._feedback_memory_count(service))
            updated = service.store.get_chat_insight(insight_id, "default")
            self.assertEqual("confirmed", updated["status"])
        finally:
            service.close()

    def test_confirm_writes_experience_per_config(self):
        service, result = self._service(experience_mode="shadow")
        self._mock_model(service)
        try:
            _, reply = self._session_with_draft(service, result)
            insight_id = reply["insights"][0]["id"]
            service.confirm_chat_insight(insight_id, _principal())
            experiences = service.store.list_experiences("default")
            self.assertTrue(any(e["experience_type"] == "rule_refinement" for e in experiences))
        finally:
            service.close()

    def test_confirm_requires_feedback_enabled(self):
        service, result = self._service(chat_feedback_enabled=False)
        self._mock_model(service)
        try:
            _, reply = self._session_with_draft(service, result)
            insight_id = reply["insights"][0]["id"]
            with self.assertRaises(ValueError):
                service.confirm_chat_insight(insight_id, _principal())
            cases = service.store.list_task_failure_cases(result["task_id"], "default")
            self.assertEqual(0, len(cases))
        finally:
            service.close()

    def test_illegal_missed_issue_location_cannot_be_confirmed(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            _, reply = self._session_with_draft(service, result)
            insight_id = reply["insights"][0]["id"]
            # Edit into a missed_issue whose line is not on a diff added line.
            updated = service.edit_chat_insight(
                insight_id, "missed_issue",
                {"rule_id": "SEC-EVAL", "path": "a.py", "line": 999},
                "缺少输入校验", _principal())
            self.assertFalse(updated["validation"]["valid"])
            with self.assertRaises(ValueError):
                service.confirm_chat_insight(insight_id, _principal())
            cases = service.store.list_task_failure_cases(result["task_id"], "default")
            self.assertEqual(0, len(cases))
            # A complete missed_issue pointing at the real added line passes.
            updated = service.edit_chat_insight(
                insight_id, "missed_issue",
                {"rule_id": "SEC-EVAL", "path": "a.py", "line": 1},
                "缺少输入校验", _principal())
            self.assertTrue(updated["validation"]["valid"])
            outcome = service.confirm_chat_insight(insight_id, _principal())
            self.assertTrue(outcome["confirmed"])
            cases = service.store.list_task_failure_cases(result["task_id"], "default")
            self.assertEqual(1, len(cases))
            self.assertEqual("missed_issue", cases[0]["category"])
        finally:
            service.close()

    def test_stale_report_blocks_confirmation(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            _, reply = self._session_with_draft(service, result)
            insight_id = reply["insights"][0]["id"]
            with service.store._connection() as conn:
                conn.execute(
                    "UPDATE tasks SET report_json=? WHERE id=?",
                    (json.dumps({"risk": "low", "findings": []}), result["task_id"]),
                )
            with self.assertRaises(ValueError):
                service.confirm_chat_insight(insight_id, _principal())
            cases = service.store.list_task_failure_cases(result["task_id"], "default")
            self.assertEqual(0, len(cases))
            session = service.store.get_chat_session(
                reply["insights"][0]["session_id"], "default")
            self.assertEqual("stale", session["status"])
        finally:
            service.close()

    def test_edit_requires_draft_and_revalidates(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            _, reply = self._session_with_draft(service, result)
            insight_id = reply["insights"][0]["id"]
            with self.assertRaises(ValueError):
                service.edit_chat_insight(insight_id, "not-a-category",
                                          {}, "note", _principal())
            updated = service.edit_chat_insight(insight_id, "bad_fix", None, "破坏兼容性", _principal())
            self.assertEqual("bad_fix", updated["category"])
            self.assertIn("破坏兼容性", updated["note"])
            service.confirm_chat_insight(insight_id, _principal())
            with self.assertRaises(ValueError):
                service.edit_chat_insight(insight_id, "accepted", {}, "x", _principal())
        finally:
            service.close()

    def test_reconciliation_restores_confirming_insights(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            _, reply = self._session_with_draft(service, result)
            insight_id = reply["insights"][0]["id"]
            # Simulate a crash right after the atomic claim: status is
            # 'confirming' but no failure case was written.
            self.assertTrue(service.store.claim_chat_insight(insight_id, "default", "tester"))
            self.assertEqual(
                "confirming",
                service.store.get_chat_insight(insight_id, "default")["status"])
            service.close()
            # A fresh service on the same database reconciles to draft because
            # no failure case exists for the source key.
            fresh = ReviewService(self.settings)
            try:
                self.assertEqual(
                    "draft",
                    fresh.store.get_chat_insight(insight_id, "default")["status"])
            finally:
                fresh.close()
            # Now simulate the feedback write committing but the process
            # crashing before the status update: reconcile to confirmed.
            service = ReviewService(self.settings)
            service.store.claim_chat_insight(insight_id, "default", "tester")
            service.store.record_failure_case(
                result["task_id"], "false_positive",
                {"finding": {}, "note": "x"},
                source_key="chat_insight:" + insight_id)
            service.close()
            reconciled = ReviewService(self.settings)
            try:
                state = reconciled.store.get_chat_insight(insight_id, "default")
                self.assertEqual("confirmed", state["status"])
                self.assertIsNotNone(state["feedback_case_id"])
            finally:
                reconciled.close()
        finally:
            service.close()

    def test_conflict_warning_for_accepted_and_false_positive(self):
        service, result = self._service()
        self._mock_model(service)
        try:
            session, reply = self._session_with_draft(service, result)
            insight_id = reply["insights"][0]["id"]  # category: false_positive
            finding = reply["insights"][0]["finding"]
            # A second insight on the same finding with the opposite category.
            service.store.create_chat_insight(
                "default", session["id"], reply["user_message_id"], result["task_id"],
                "accepted", finding, "符合预期", 0.9, {"source": "test"}, status="draft",
            )
            updated = service.edit_chat_insight(insight_id, None, None, "说明", _principal())
            self.assertTrue(any("误报" in w for w in updated["validation"].get("warnings", [])))
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
