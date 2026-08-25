"""Tests for evolution budget and dedup guard (plan section 10.6)."""
import unittest

from evoagent.evolution_gov.budget import (
    BudgetDenied,
    EvolutionBudget,
    EvolutionBudgetGuard,
)


class EvolutionBudgetGuardTest(unittest.TestCase):

    def test_candidate_cap_enforced(self):
        budget = EvolutionBudget(max_candidates_per_day=2)
        guard = EvolutionBudgetGuard(budget)
        guard.register_candidate("a")
        guard.register_candidate("b")
        with self.assertRaises(BudgetDenied):
            guard.register_candidate("c")

    def test_replay_budget_enforced(self):
        budget = EvolutionBudget(max_replay_cases=3)
        guard = EvolutionBudgetGuard(budget)
        guard.register_replay(3)
        with self.assertRaises(BudgetDenied):
            guard.register_replay(1)

    def test_active_experiment_cap(self):
        budget = EvolutionBudget(max_active_experiments=1)
        guard = EvolutionBudgetGuard(budget)
        guard.begin_experiment("exp-1")
        with self.assertRaises(BudgetDenied):
            guard.begin_experiment("exp-2")
        guard.close_experiment("exp-1")
        guard.begin_experiment("exp-2")  # now allowed

    def test_activation_cap_and_cooldown(self):
        budget = EvolutionBudget(max_activations_per_day=1,
                                 cooldown_seconds=100.0)
        guard = EvolutionBudgetGuard(budget)
        guard.record_activation("c", now=100.0)
        with self.assertRaises(BudgetDenied):
            guard.record_activation("c", now=130.0)  # within cooldown
        # Day cap of 1 already reached, so a different candidate is denied too.
        with self.assertRaises(BudgetDenied):
            guard.record_activation("d", now=200.0)

    def test_dedup_signature(self):
        guard = EvolutionBudgetGuard()
        self.assertTrue(guard.dedupe_signature("v3-content"))
        self.assertFalse(guard.dedupe_signature("v3-content"))

    def test_failed_hypothesis_blacklisted(self):
        guard = EvolutionBudgetGuard()
        for _ in range(3):
            guard.record_hypothesis_failure("H-99")
        guard.record_hypothesis_failure("H-99")  # 4th -> >=3
        blacklisted = guard.blacklist_default_failed()
        self.assertIn("H-99", blacklisted)

    def test_blacklisted_hypothesis_denied(self):
        budget = EvolutionBudget(blacklist_hypotheses=["H-bad"])
        guard = EvolutionBudgetGuard(budget)
        decision = guard.check_candidate("c", hypothesis_id="H-bad")
        self.assertFalse(decision.allowed)
        self.assertIn("black-listed", decision.reason)


if __name__ == "__main__":
    unittest.main()