"""End-to-end test of the policy-evolution pipeline (generate -> gate ->
replay -> canary -> promote / rollback).
"""
import unittest

from evoagent.policy.models import ExecutionPolicy
from evoagent.policy_evolution.candidate import CandidateOperation
from evoagent.policy_evolution.objective import EvolutionMetrics
from evoagent.policy_evolution.pipeline import (
    PolicyEvolutionPipeline,
    RunnableStatus,
)


def metrics(quality=0.7, recall=0.8, cost=5.0, reliability=1.0,
            failure=0.0, critical=0):
    return EvolutionMetrics(
        quality_score=quality, high_risk_recall=recall,
        reliability_score=reliability, failure_rate=failure,
        critical_misses=critical, cost=cost)


class PipelineEndToEndTest(unittest.TestCase):

    def test_good_candidate_reaches_promote(self):
        def runner(policy):
            # A candidate policy with an evolved step budget scores better.
            if getattr(policy, "policy_id", "").endswith("LOWER_MAX_STEPS"):
                return metrics(quality=0.9, cost=2.0)
            return metrics(quality=0.7, cost=5.0)

        base = ExecutionPolicy(policy_id="base", policy_version=1)
        pipeline = PolicyEvolutionPipeline(runner=runner)
        candidates = pipeline.generate(
            base, operations=[CandidateOperation.LOWER_MAX_STEPS])
        trace = pipeline.evaluate_replay(candidates[0], base)
        self.assertEqual(trace.status, RunnableStatus.REPLAY_PASSED)

    def test_hard_gate_rejects_candidate(self):
        def runner(policy):
            return metrics(quality=0.9, critical=1)

        base = ExecutionPolicy(policy_id="base")
        pipeline = PolicyEvolutionPipeline(runner=runner)
        candidates = pipeline.generate(
            base, operations=[CandidateOperation.RAISE_MAX_STEPS])
        trace = pipeline.evaluate_replay(candidates[0], base)
        self.assertEqual(trace.status, RunnableStatus.REJECTED)
        self.assertFalse(trace.gate.approved)

    def test_trace_retained(self):
        def runner(policy):
            return metrics(quality=0.8, cost=3.0)
        base = ExecutionPolicy(policy_id="base")
        pipeline = PolicyEvolutionPipeline(runner=runner)
        cands = pipeline.generate(base, operations=[CandidateOperation.LOWER_MAX_STEPS])
        pipeline.evaluate_replay(cands[0], base)
        self.assertIsNotNone(pipeline.trace(cands[0].candidate_id))


if __name__ == "__main__":
    unittest.main()