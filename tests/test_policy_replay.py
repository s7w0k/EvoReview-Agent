"""Tests for replay-based policy comparison (plan section 9.6)."""
import unittest

from evoagent.policy.models import ExecutionPolicy
from evoagent.policy_evolution.objective import EvolutionMetrics
from evoagent.policy_evolution.replay_eval import PolicyReplayEvaluator


def runner_for(quality=0.7, recall=0.8, cost=5.0, latency=2.0,
               failure_rate=0.0, steps=20, calls=15):
    def runner(_policy):
        return EvolutionMetrics(
            quality_score=quality,
            high_risk_recall=recall,
            failure_rate=failure_rate,
            cost=cost,
            latency=latency,
            tool_calls=calls,
            agent_steps=steps,
        )
    return runner


class PolicyReplayEvaluationTest(unittest.TestCase):

    def test_evaluates_both_policies(self):
        base = ExecutionPolicy(policy_id="base")
        cand = ExecutionPolicy(policy_id="cand")
        evaluator = PolicyReplayEvaluator(runner_for(quality=0.7, cost=5.0))
        out = evaluator.evaluate(base, cand)

        self.assertAlmostEqual(out.baseline.quality_score, 0.7)
        self.assertAlmostEqual(out.candidate.quality_score, 0.7)
        self.assertGreaterEqual(out.utility, 0.0)

    def test_cheaper_candidate_scores_higher_utility(self):
        from evoagent.policy_evolution.objective import evolution_utility
        baseline = EvolutionMetrics(
            quality_score=0.8, high_risk_recall=0.8, reliability_score=1.0,
            cost=5.0, latency=2.0)
        expensive = EvolutionMetrics(
            quality_score=0.8, high_risk_recall=0.8, reliability_score=1.0,
            cost=5.0, latency=2.0)
        cheap = EvolutionMetrics(
            quality_score=0.8, high_risk_recall=0.8, reliability_score=1.0,
            cost=1.0, latency=0.5)
        # Same quality, but the cheaper candidate must score higher utility.
        self.assertGreater(
            evolution_utility(cheap, reference=baseline),
            evolution_utility(expensive, reference=baseline))

    def test_replay_reports_metric_deltas(self):
        base = ExecutionPolicy(policy_id="base")
        evaluator = PolicyReplayEvaluator(runner_for(steps=20, calls=15))
        out = evaluator.evaluate(base, ExecutionPolicy(policy_id="c"))
        self.assertTrue(any("quality" in d for d in out.deltas))
        self.assertTrue(any("tool_calls" in d for d in out.deltas))


if __name__ == "__main__":
    unittest.main()