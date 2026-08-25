import os
import tempfile
import unittest

from evoagent.config import Settings
from evoagent.service import ReviewService


class ServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.settings = Settings(
            host="127.0.0.1", port=8080, db_path=self.path, max_diff_bytes=10000,
            max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="", llm_model="",
            github_webhook_secret="", github_token="", auto_post_review=False,
        )

    def tearDown(self):
        os.unlink(self.path)

    def test_end_to_end_review(self):
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(data)\n"
        service = ReviewService(self.settings)
        result = service.create_review("org/repo", diff, 1)
        task = service.store.get(result["task_id"])
        service.queue.close()
        self.assertEqual("SUCCESS", result["state"])
        self.assertEqual("SEC-EVAL", result["report"]["findings"][0]["rule_id"])
        self.assertEqual(
            "plan-challenge-revise-evidence-verify-arbitrate",
            result["report"]["collaboration"]["protocol"],
        )
        self.assertGreater(result["report"]["collaboration"]["messages"], 0)
        self.assertIn(
            "arbitration_decision", {item["kind"] for item in task["collaboration"]}
        )

    def test_rejects_large_diff(self):
        service = ReviewService(self.settings)
        with self.assertRaises(ValueError):
            service.create_review("org/repo", "x" * 10001)

    def test_service_close_is_idempotent(self):
        service = ReviewService(self.settings)
        service.close()
        service.close()
        # The existing queue.close() call must remain valid after close().
        service.queue.close()
        service.queue.close()

    def test_completed_review_feedback_is_persisted_and_listed_per_task(self):
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(data)\n"
        service = ReviewService(self.settings)
        result = service.create_review("org/repo", diff, 1)
        task_id = result["task_id"]

        feedback = service.record_feedback(
            task_id, "false_positive", result["report"]["findings"][0], "不是实际风险",
        )

        self.assertEqual({"recorded": True, "category": "false_positive"}, feedback)
        cases = service.store.list_task_failure_cases(task_id, "default")
        self.assertEqual(1, len(cases))
        self.assertEqual("false_positive", cases[0]["category"])
        self.assertEqual("SEC-EVAL", cases[0]["payload"]["finding"]["rule_id"])
        service.queue.close()


# Work Package 0: freeze the existing feedback contract before the report-chat
# feature is introduced. Any change to these guarantees must be intentional.
class FeedbackContractTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.settings = Settings(
            host="127.0.0.1", port=8080, db_path=self.path, max_diff_bytes=10000,
            max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="", llm_model="",
            github_webhook_secret="", github_token="", auto_post_review=False,
        )

    def tearDown(self):
        os.unlink(self.path)

    def _completed_task(self, service, tenant="default"):
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(data)\n"
        return service.create_review("org/repo", diff, 1, tenant_id=tenant)

    def test_rejects_feedback_for_non_success_task(self):
        service = ReviewService(self.settings)
        service.store.create("task-danger", "org/repo", 1, {}, "default")
        service.store.save_task_payload(
            "task-danger", "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(data)\n"
        )
        # state is pending, not "SUCCESS + report".
        try:
            with self.assertRaises(ValueError):
                service.record_feedback("task-danger", "false_positive", {}, "note")
        finally:
            service.close()

    def test_all_categories_accepted(self):
        service = ReviewService(self.settings)
        result = self._completed_task(service)
        task_id = result["task_id"]
        finding = result["report"]["findings"][0]
        try:
            for category in ("false_positive", "missed_issue", "bad_fix", "accepted"):
                service.record_feedback(task_id, category, finding, "note for %s" % category)
            cases = service.store.list_task_failure_cases(task_id, "default")
            self.assertEqual(4, len(cases))
            self.assertEqual(
                {"false_positive", "missed_issue", "bad_fix", "accepted"},
                {c["category"] for c in cases},
            )
        finally:
            service.close()

    def test_unsupported_category_rejected(self):
        service = ReviewService(self.settings)
        result = self._completed_task(service)
        try:
            with self.assertRaises(ValueError):
                service.record_feedback(result["task_id"], "nonsense", {}, "note")
        finally:
            service.close()

    def test_feedback_writes_semantic_memory(self):
        service = ReviewService(self.settings)
        result = self._completed_task(service)
        task_id = result["task_id"]
        try:
            service.record_feedback(
                task_id, "false_positive", result["report"]["findings"][0], "FP note"
            )
            memories = service.store.list_agent_memories("default", "org/repo", ("semantic",))
            self.assertTrue(any(m["kind"] == "review_feedback" for m in memories))
        finally:
            service.close()

    def test_failure_cases_isolated_by_tenant(self):
        service = ReviewService(self.settings)
        result = self._completed_task(service, tenant="tenant-a")
        task_id = result["task_id"]
        try:
            service.record_feedback(
                task_id, "false_positive", result["report"]["findings"][0], "note"
            )
            self.assertEqual(1, len(service.store.list_task_failure_cases(task_id, "tenant-a")))
            self.assertEqual(0, len(service.store.list_task_failure_cases(task_id, "tenant-b")))
        finally:
            service.close()

    def test_feedbacker_identity_is_persisted(self):
        service = ReviewService(self.settings)
        result = self._completed_task(service)
        task_id = result["task_id"]
        try:
            service.record_feedback(
                task_id, "false_positive", result["report"]["findings"][0], "note",
                "default", feedbacker="user-42",
            )
            cases = service.store.list_task_failure_cases(task_id, "default")
            self.assertEqual("user-42", cases[0]["payload"].get("feedbacker"))
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
