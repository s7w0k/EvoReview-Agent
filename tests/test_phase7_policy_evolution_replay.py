"""Phase 7 acceptance tests: policy evolution wired to the real replay dataset.

Covers:
  * evolvable-field whitelist + fail-closed enforcement (11.1)
  * candidate mutation recording (changed_fields / before / after) + signature (11.2/11.3)
  * deterministic dataset split (train / validation / holdout / temporal) (11.5)
  * real PolicyReplayRunner aggregating the 12 metrics from snapshots (11.4/11.6)
  * extended hard gate: policy violations + side-effect incidents (11.7)
"""
import unittest

from evoagent.policy.models import ExecutionPolicy, ToolPermission
from evoagent.policy.models import ExecutionBudget
from evoagent.policy_evolution.candidate import (
    CandidateOperation,
    PolicyCandidateGenerator,
    candidate_signature,
)
from evoagent.policy_evolution.dataset import split_dataset
from evoagent.policy_evolution.evolution_scope import (
    ForbiddenEvolutionField,
    assert_evolvable,
    validate_mutated_fields,
)
from evoagent.policy_evolution.gate import EvolutionGate
from evoagent.policy_evolution.objective import EvolutionMetrics
from evoagent.policy_evolution.runner import PolicyReplayRunner
from evoagent.replay.models import ReplaySnapshot


def make_policy(allow=("search_code", "read_file")):
    return ExecutionPolicy(
        policy_id="base",
        policy_version=1,
        tool_permissions=[ToolPermission(tool_name=t) for t in allow],
    )


def make_snapshot(*, findings=(), tool_observations=(), task_id="t", created=1.0,
                  baseline=None):
    return ReplaySnapshot(
        task_id=task_id,
        created_at=created,
        tool_observations=[{"tool_name": obs} for obs in tool_observations],
        expected_output={
            "findings": findings,
            "baseline": baseline or {
                "tp": 2, "fp": 0, "fn": 0, "tn": 2,
                "tool_calls": len(tool_observations) or 2,
                "agent_steps": 3, "latency_ms": 100, "cost": 0.5,
                "recovery_attempts": 0, "recovery_successes": 0, "failure": False,
            },
        },
    )


class EvolutionScopeTest(unittest.TestCase):
    def test_whitelisted_field_ok(self):
        assert_evolvable(["enabled_agents", "max_steps"])
        self.assertEqual(
            validate_mutated_fields(["enabled_agents", "max_steps"]), [])

    def test_forbidden_field_detected(self):
        self.assertEqual(
            validate_mutated_fields(["auth_required", "secret_handling"]),
            ["auth_required", "secret_handling"])

    def test_fail_closed_on_unknown_field(self):
        with self.assertRaises(ForbiddenEvolutionField):
            assert_evolvable(["unknown_knob"])


class CandidateMutationTest(unittest.TestCase):
    def test_signature_dedupe_same_parent_op(self):
        parent = make_policy()
        sig1 = candidate_signature(parent, CandidateOperation.LOWER_MAX_STEPS)
        sig2 = candidate_signature(parent, CandidateOperation.LOWER_MAX_STEPS)
        self.assertEqual(sig1, sig2)
        other = candidate_signature(parent, CandidateOperation.RAISE_MAX_STEPS)
        self.assertNotEqual(sig1, other)

    def test_generator_records_before_after(self):
        parent = ExecutionPolicy(
            policy_id="base",
            policy_version=1,
            budget=ExecutionBudget(max_steps=7, max_tool_calls=9),
        )
        cand = PolicyCandidateGenerator("cd").generate(
            parent, operations=[CandidateOperation.LOWER_MAX_STEPS])[0]
        self.assertIn("max_steps", cand.changed_fields)
        before, after = cand.changed_fields["max_steps"]
        self.assertEqual(before, 7)
        self.assertEqual(after, 5)
        self.assertTrue(cand.signature)
        self.assertEqual(cand.scope, "runtime")


class DatasetSplitTest(unittest.TestCase):
    def test_partitions_are_disjoint_and_generation_separated(self):
        snaps = [make_snapshot(task_id=f"task-{i}", created=float(i))
                 for i in range(20)]
        split = split_dataset(snaps, temporal_holdout_ratio=0.2,
                              holdout_ratio=0.1)
        self.assertTrue(split.temporal_holdout)  # old 20% (created 0..3)
        # Holdout / train / validation must never contain temporal items.
        generation_ids = {s.task_id for s in split.generation_pool}
        holdout_ids = {s.task_id for s in split.holdout}
        temporal_ids = {s.task_id for s in split.temporal_holdout}
        self.assertTrue(generation_ids.isdisjoint(temporal_ids))
        self.assertTrue(generation_ids.isdisjoint(holdout_ids))
        self.assertEqual(len(split), len(snaps))

    def test_task_stability(self):
        snaps = [make_snapshot(task_id=f"same", created=float(i))
                 for i in range(10)]
        s1 = split_dataset(snaps, holdout_ratio=0.3)
        s2 = split_dataset(snaps, holdout_ratio=0.3)
        self.assertEqual(
            {x.task_id for x in s1.holdout}, {x.task_id for x in s2.holdout})


class PolicyReplayRunnerTest(unittest.TestCase):
    def test_metrics_aggregated_across_snapshots(self):
        snapshots = [
            make_snapshot(
                task_id="a",
                findings=[
                    {"tool": "search_code", "severity": "critical", "detected": True},
                    {"tool": "search_code", "severity": "high", "detected": True},
                ],
                tool_observations=["search_code", "read_file"],
            ),
            make_snapshot(
                task_id="b",
                findings=[
                    {"tool": "search_code", "severity": "high", "detected": False},
                ],
                tool_observations=["search_code"],
                baseline={"tp": 1, "fp": 0, "fn": 1, "tn": 2,
                          "tool_calls": 1, "agent_steps": 2, "latency_ms": 80,
                          "cost": 0.4, "recovery_attempts": 0,
                          "recovery_successes": 0, "failure": False},
            ),
        ]
        runner = PolicyReplayRunner(snapshots)
        # Policy allows search_code, denies read_file.
        policy = make_policy(allow=("search_code",))
        metrics = runner.run(policy)
        # Search_code is allowed everywhere, so no critical miss and full recall.
        self.assertEqual(metrics.critical_misses, 0)
        # read_file is recorded but denied -> a policy violation.
        self.assertGreater(metrics.policy_violations, 0)

    def test_denied_critical_tool_creates_critical_miss(self):
        snapshots = [
            make_snapshot(
                task_id="c",
                findings=[
                    {"tool": "credential_scanner", "severity": "critical",
                     "detected": True},
                ],
                tool_observations=["credential_scanner"],
                baseline={"tp": 1, "fp": 0, "fn": 0, "tn": 0,
                          "tool_calls": 1, "agent_steps": 1, "latency_ms": 50,
                          "cost": 0.2, "recovery_attempts": 0,
                          "recovery_successes": 0, "failure": False},
            ),
        ]
        policy = make_policy(allow=("search_code",))  # credential_scanner denied
        metrics = PolicyReplayRunner(snapshots).run(policy)
        self.assertEqual(metrics.critical_misses, 1)
        self.assertEqual(metrics.policy_violations, 1)
        self.assertLess(metrics.high_risk_recall, 1.0)

    def test_exposes_twelve_metrics(self):
        snapshots = [make_snapshot(
            task_id="d",
            findings=[
                {"tool": "search_code", "severity": "high", "detected": True}],
            tool_observations=["search_code"],
        )]
        m = PolicyReplayRunner(snapshots).run(make_policy())
        fields = {
            "finding_f1", "high_risk_recall", "critical_misses",
            "false_positive_rate", "task_success_rate", "failure_rate",
            "recovery_success_rate", "tool_calls", "agent_steps",
            "latency", "cost", "policy_violations",
        }
        for name in fields:
            self.assertIn(name, vars(m), name)


class HardGateSafetyTest(unittest.TestCase):
    def good(self, **over):
        value = {"quality_score": 0.8, "high_risk_recall": 0.9,
                 "critical_misses": 0, "reliability_score": 1.0}
        value.update(over)
        return EvolutionMetrics(**value)

    def test_zero_policy_violation_required(self):
        gate = EvolutionGate()
        base = self.good()
        cand = self.good(policy_violations=1, cost=0.0)
        decision = gate.evaluate(base, cand)
        self.assertFalse(decision.approved)
        self.assertTrue(any("policy violation" in r for r in decision.reasons))

    def test_zero_side_effect_incident_required(self):
        gate = EvolutionGate()
        cand = self.good(side_effect_safety_incidents=1)
        self.assertFalse(gate.evaluate(self.good(), cand).approved)


if __name__ == "__main__":
    unittest.main()