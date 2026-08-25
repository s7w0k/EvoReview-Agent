"""Tests for template-based policy candidate generation (plan section 9.5)."""
import unittest

from evoagent.policy.models import AgentPolicy, ExecutionPolicy
from evoagent.policy_evolution.candidate import (
    CandidateOperation,
    PolicyCandidateGenerator,
)


class PolicyCandidateGeneratorTest(unittest.TestCase):

    def test_generate_produces_derived_candidates(self):
        parent = ExecutionPolicy(
            policy_id="p",
            policy_version=3,
            agents=AgentPolicy(
                enabled_agents=["security", "reliability"],
                max_parallel_agents=2),
        )
        generator = PolicyCandidateGenerator("cand")
        candidates = generator.generate(parent)
        self.assertTrue(len(candidates) >= 1)
        for cand in candidates:
            self.assertEqual(cand.parent_policy_id, "p")
            self.assertGreater(cand.policy.policy_version, 3)
            self.assertIn(cand.operation,
                          list(CandidateOperation))

    def test_remove_agent_template(self):
        parent = ExecutionPolicy(
            policy_id="p",
            agents=AgentPolicy(enabled_agents=["security", "reliability"]),
        )
        gen = PolicyCandidateGenerator("x")
        cands = gen.generate(
            parent, operations=[CandidateOperation.REMOVE_AGENT],
            remove_agent="security")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].policy.agents.enabled_agents,
                         ["reliability"])

    def test_add_agent_template(self):
        parent = ExecutionPolicy(
            policy_id="p",
            agents=AgentPolicy(enabled_agents=["reliability"]),
        )
        gen = PolicyCandidateGenerator("x")
        cands = gen.generate(
            parent, operations=[CandidateOperation.ADD_AGENT],
            add_agent="semantic")
        self.assertEqual(cands[0].policy.agents.enabled_agents,
                         ["reliability", "semantic"])

    def test_lower_and_raise_max_steps(self):
        from evoagent.policy.models import ExecutionBudget
        parent = ExecutionPolicy(
            policy_id="p", budget=ExecutionBudget(max_steps=6))
        gen = PolicyCandidateGenerator("x")

        cands = gen.generate(
            parent, operations=[CandidateOperation.LOWER_MAX_STEPS], step_delta=2)
        self.assertEqual(cands[0].policy.budget.max_steps, 4)

        cands2 = gen.generate(
            parent, operations=[CandidateOperation.RAISE_MAX_STEPS], step_delta=2)
        self.assertEqual(cands2[0].policy.budget.max_steps, 8)

    def test_toggle_evidence(self):
        from evoagent.policy.models import VerificationPolicy
        parent = ExecutionPolicy(
            policy_id="p", verification=VerificationPolicy(evidence_required=False))
        gen = PolicyCandidateGenerator("x")

        enabled = gen.generate(
            parent, operations=[CandidateOperation.ENABLE_EVIDENCE])
        self.assertTrue(enabled[0].policy.verification.evidence_required)

        disabled = gen.generate(
            parent, operations=[CandidateOperation.DISABLE_EVIDENCE])
        self.assertFalse(disabled[0].policy.verification.evidence_required)

    def test_candidate_serializes(self):
        parent = ExecutionPolicy(policy_id="p", agents=AgentPolicy(enabled_agents=["a"]))
        cand = PolicyCandidateGenerator("c").generate(
            parent, operations=[CandidateOperation.REMOVE_AGENT], remove_agent="a")[0]
        data = cand.to_dict()
        self.assertEqual(data["parent_policy_id"], "p")
        self.assertIn("policy", data)


if __name__ == "__main__":
    unittest.main()