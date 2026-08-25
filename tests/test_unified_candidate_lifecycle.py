"""Closed-loop WP4: unified Prompt/Skill candidate lifecycle and approval policy."""
import os
import tempfile
import unittest

from evoagent import candidate_lifecycle as cl
from evoagent import evolution_policy as policy
from evoagent import skill_lifecycle as sl
from evoagent.rollout import ReleaseManager
from evoagent.skill_evolution import SkillEvolutionEngine
from evoagent.store import TaskStore

RISK_DIFF = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+dangerous_call(data)\n"
CLEAN_DIFF = "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+safe_call(data)\n"


def artifact():
    return {
        "name": "evolved-review",
        "rules": [{
            "rule_id": "SEC-DANGEROUS-CALL", "severity": "high",
            "match": "dangerous_call(data)", "title": "Dangerous call",
            "explanation": "The confirmed dangerous API was added.",
            "fix": "Use safe_call instead.", "test": "Add a regression test.",
        }],
    }


class LifecycleStateMachineTests(unittest.TestCase):
    def test_draft_cannot_jump_to_active(self):
        self.assertFalse(sl.can_transition(sl.DRAFT, sl.ACTIVE))
        self.assertFalse(cl.permitted(cl.ACTOR_DEPLOYMENT, sl.VALIDATED, sl.ACTIVE))

    def test_unified_states_are_valid(self):
        for status in (sl.QUARANTINED, sl.EVALUATING, sl.SHADOW, sl.CANARY,
                       sl.STALE, sl.ROLLED_BACK):
            self.assertTrue(sl.is_valid(status))

    def test_deployment_promotion_path(self):
        self.assertTrue(sl.can_transition(sl.VALIDATED, sl.SHADOW))
        self.assertTrue(sl.can_transition(sl.SHADOW, sl.CANARY))
        self.assertTrue(sl.can_transition(sl.CANARY, sl.ACTIVE))


class ActorPermissionTests(unittest.TestCase):
    def test_deployment_controller_only_promotes(self):
        self.assertTrue(cl.permitted(cl.ACTOR_DEPLOYMENT, sl.VALIDATED, sl.SHADOW))
        self.assertTrue(cl.permitted(cl.ACTOR_DEPLOYMENT, sl.SHADOW, sl.CANARY))
        self.assertTrue(cl.permitted(cl.ACTOR_DEPLOYMENT, sl.CANARY, sl.ACTIVE))
        self.assertFalse(cl.permitted(cl.ACTOR_DEPLOYMENT, sl.VALIDATED, sl.ACTIVE))

    def test_evaluator_only_decides_evaluation(self):
        self.assertTrue(cl.permitted(cl.ACTOR_EVALUATOR, sl.EVALUATING, sl.VALIDATED))
        self.assertTrue(cl.permitted(cl.ACTOR_EVALUATOR, sl.EVALUATING, sl.REJECTED))
        self.assertFalse(cl.permitted(cl.ACTOR_EVALUATOR, sl.VALIDATED, sl.SHADOW))

    def test_admin_and_rollback_policy_roll_back(self):
        for actor in (cl.ACTOR_ADMIN, cl.ACTOR_ROLLBACK):
            self.assertTrue(cl.permitted(actor, sl.ACTIVE, sl.ROLLED_BACK))
            self.assertTrue(cl.permitted(actor, sl.CANARY, sl.ROLLED_BACK))
            self.assertTrue(cl.permitted(actor, sl.SHADOW, sl.ROLLED_BACK))

    def test_admin_emergency_restores_historical_version(self):
        self.assertTrue(cl.permitted(
            cl.ACTOR_ADMIN, sl.VALIDATED, sl.ACTIVE, is_historical=True))
        self.assertFalse(cl.permitted(cl.ACTOR_ADMIN, sl.VALIDATED, sl.ACTIVE))

    def test_builder_agent_reflection_cannot_promote(self):
        for actor in (cl.ACTOR_BUILDER, cl.ACTOR_AGENT, cl.ACTOR_REFLECTION):
            self.assertFalse(cl.permitted(actor, sl.VALIDATED, sl.SHADOW))
            self.assertFalse(cl.permitted(actor, sl.CANARY, sl.ACTIVE))


class ApprovalPolicyTests(unittest.TestCase):
    def test_always(self):
        self.assertTrue(policy.requires_approval(policy.APPROVAL_ALWAYS, risk_level="low"))

    def test_high_risk(self):
        self.assertTrue(policy.requires_approval(policy.APPROVAL_HIGH_RISK, risk_level="high"))
        self.assertTrue(policy.requires_approval(policy.APPROVAL_HIGH_RISK, risk_level="low", cross_repo=True))
        self.assertFalse(policy.requires_approval(policy.APPROVAL_HIGH_RISK, risk_level="low"))

    def test_never(self):
        self.assertFalse(policy.requires_approval(policy.APPROVAL_NEVER, risk_level="high"))


class ReleaseManagerPromotionTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)
        self.releases = ReleaseManager(self.store)

    def tearDown(self):
        os.unlink(self.path)

    def _seed_validated(self):
        return self.store.save_skill_artifact(
            "evolved-review", artifact(), 0.9, False, "default", status="validated",
        )

    def test_promotes_validated_to_active_through_shadow_canary(self):
        version = self._seed_validated()["version"]
        self.assertTrue(self.releases.promote_candidate("default", "evolved-review", version))
        self.assertEqual("shadow", self.releases._version_status("default", "evolved-review", version))
        self.assertTrue(self.releases.promote_candidate("default", "evolved-review", version))
        self.assertEqual("canary", self.releases._version_status("default", "evolved-review", version))
        self.assertTrue(self.releases.promote_candidate("default", "evolved-review", version))
        self.assertEqual("active", self.releases._version_status("default", "evolved-review", version))

    def test_rollback_candidate(self):
        version = self._seed_validated()["version"]
        self.releases.promote_candidate("default", "evolved-review", version)  # -> shadow
        self.assertTrue(self.releases.rollback_candidate("default", "evolved-review", version))
        self.assertEqual("rolled_back", self.releases._version_status("default", "evolved-review", version))


class SkillEngineProductionProfileTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)
        self.store.save_evaluation_case(
            "danger-validation", "validation", RISK_DIFF,
            [{"path": "a.py", "line": 1, "rule_id": "SEC-DANGEROUS-CALL",
              "min_severity": "high"}], "test",
        )
        self.store.save_evaluation_case("clean-holdout", "holdout", CLEAN_DIFF, [], "test")

    def tearDown(self):
        os.unlink(self.path)

    def test_production_profile_validates_without_activating(self):
        engine = SkillEvolutionEngine(
            self.store, min_cases=1, max_cases=10, min_improvement=.01,
            min_holdout_cases=1, production_profile=True,
        )
        result = engine.propose("evolved-review", artifact())
        self.assertEqual("validated", result["decision"])
        self.assertEqual(sl.VALIDATED, result["version"]["status"])
        self.assertFalse(result["version"]["active"])

    def test_dev_profile_still_activates(self):
        engine = SkillEvolutionEngine(
            self.store, min_cases=1, max_cases=10, min_improvement=.01,
            min_holdout_cases=1, production_profile=False,
        )
        result = engine.propose("evolved-review", artifact())
        self.assertEqual("activated", result["decision"])


if __name__ == "__main__":
    unittest.main()
