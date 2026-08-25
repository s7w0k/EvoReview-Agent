"""Phase 1 acceptance tests: policy really drives the ReviewService pipeline.

Covers (plan section 5):
  5.4  Per-request policy resolution produces a frozen ``ReviewExecutionContext``.
  5.5  ReviewHarness / AgentRuntime take the resolved execution policy.
  5.7  Dynamic agent routing + verification gating reflect the policy.
      - low risk   -> no critic / evidence / verifier stages
      - high risk  -> critic + evidence + verifier all run
  E2E: a real ``/v1/reviews`` request persists the collaboration transcript
  whose stages match the resolved risk policy.
"""
import os
import tempfile
import unittest

from evoagent.agents import MultiAgentCoordinator
from evoagent.config import Settings
from evoagent.diff_parser import parse_unified_diff
from evoagent.policy import RiskProfiler
from evoagent.policy.defaults import default_policy
from evoagent.reviewer import LocalRuleReviewer, Reviewer
from evoagent.service import ReviewService


def make_settings(path):
    return Settings(
        host="127.0.0.1", port=8080, db_path=path, max_diff_bytes=10000,
        max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="", llm_model="",
        github_webhook_secret="", github_token="", auto_post_review=False,
        skills_dir="skills", eval_min_holdout_cases=0,
    )


class _Agent(Reviewer):
    name = "reliability"

    def review(self, diff, parsed):
        return []


LOW_DIFF = (
    "--- a/app.py\n+++ b/app.py\n"
    "@@ -1 +1,2 @@\n"
    "-old\n"
    "+def helper():\n"
    "+    pass\n"
)

HIGH_DIFF = (
    "--- a/auth/security.py\n+++ b/auth/security.py\n"
    "@@ -1 +1,2 @@\n"
    "-old\n"
    "+result = eval(request.body)\n"
)


class RuntimeNodeGatingTests(unittest.TestCase):
    """5.7: node graph expands with the resolved risk level."""

    def node_names(self, execution_policy):
        coordinator = MultiAgentCoordinator(
            [_Agent()], execution_policy=execution_policy,
        )
        return [node.name for node in coordinator._runtime_nodes()]

    def test_low_skips_verification_stages(self):
        names = self.node_names(default_policy("low"))
        self.assertNotIn("deliberation", names)
        self.assertNotIn("evidence", names)
        self.assertNotIn("verifier", names)
        self.assertIn("planner", names)
        self.assertIn("specialists", names)
        self.assertIn("arbiter", names)

    def test_high_runs_all_verification_stages(self):
        names = self.node_names(default_policy("high"))
        for stage in ("deliberation", "evidence", "verifier"):
            self.assertIn(stage, names)


class ReviewServicePolicyRoutingTests(unittest.TestCase):
    """5.4 / E2E: ReviewService resolves policy and persists the routing."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        if os.path.exists(self.path):
            try:
                os.unlink(self.path)
            except PermissionError:
                pass

    def _collab_kinds(self, service, task_id):
        task = service.store.get(task_id) or {}
        return [
            item.get("kind", "")
            for item in (task.get("collaboration") or [])
        ]

    def test_context_resolves_policy_and_runtime_version(self):
        service = ReviewService(make_settings(self.path))
        try:
            ctx = service._resolve_execution_context(
                "t1", "org/repo", 2, HIGH_DIFF, "default",
            )
            self.assertEqual(ctx.risk_level, "critical")
            self.assertTrue(
                ctx.execution_policy.verification.critic_required
            )
            self.assertIsNotNone(ctx.runtime_policy_version)
            self.assertEqual(
                ctx.policy_id, ctx.execution_policy.policy_id,
            )
        finally:
            service.queue.close()

    def test_low_risk_routing_omits_verification_stages(self):
        service = ReviewService(make_settings(self.path))
        try:
            result = service.create_review("org/repo", LOW_DIFF, 2)
            kinds = set(self._collab_kinds(service, result["task_id"]))
            self.assertNotIn("critique_for_reflection", kinds)
            self.assertNotIn("evidence_report", kinds)
            self.assertNotIn("verification_decision", kinds)
            self.assertIn("arbitration_decision", kinds)
        finally:
            service.queue.close()

    def test_high_risk_routing_runs_all_verification_stages(self):
        service = ReviewService(make_settings(self.path))
        try:
            result = service.create_review("org/repo", HIGH_DIFF, 2)
            kinds = set(self._collab_kinds(service, result["task_id"]))
            for stage in (
                "critique_for_reflection",
                "evidence_report",
                "verification_decision",
                "arbitration_decision",
            ):
                self.assertIn(stage, kinds)
        finally:
            service.queue.close()

    def test_low_risk_maps_to_reliability_only(self):
        parsed = parse_unified_diff(LOW_DIFF)
        risk = RiskProfiler().profile(parsed)
        self.assertEqual(risk.level, "low")


if __name__ == "__main__":
    unittest.main()