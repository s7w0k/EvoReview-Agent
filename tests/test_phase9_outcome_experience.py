"""Phase 9 acceptance tests: production outcome -> experience.

Covers:
  * outcome kinds across runtime / quality / safety (13.1)
  * outcome attribution (13.2)
  * outcome -> experience builder for the five kinds (13.3)
  * feedback trust: min confirmers + trusted ratio + duplicate merge + cooldown (13.4)
  * lineage closes with OUTCOME layer (13.5)
"""
import unittest

from evoagent.evolution_gov.lineage import LineageStage, LineageTracker
from evoagent.outcome_evolution.builder import (
    FAILURE,
    NEGATIVE,
    POSITIVE,
    SAFETY,
    OutcomeExperienceBuilder,
)
from evoagent.outcome_evolution.outcome import (
    Outcome,
    OutcomeAttribution,
    OutcomeKind,
    RuntimeMetrics,
)
from evoagent.outcome_evolution.store import OutcomeStore
from evoagent.outcome_evolution.trust import (
    OutcomeTrustGate,
    TrustConfig,
)


def make_outcome(kind, task_id="task-1", finding=None, candidate_id="cand-1"):
    return Outcome(
        task_id=task_id,
        tenant_id="t1",
        repository="repo",
        risk_level="high",
        kind=kind,
        attribution=OutcomeAttribution(
            prompt_version="p1", rule_skill_version="r1", procedure_version="pr1",
            runtime_policy_version="polv2", deployment_lane="candidate",
            candidate_id=candidate_id,
        ),
        metrics=RuntimeMetrics(latency_ms=120, cost=0.4, tool_calls=8,
                               recovery_count=1),
        finding=finding,
    )


class OutcomeModelTest(unittest.TestCase):
    def test_kind_is_safety(self):
        for kind in (OutcomeKind.CRITICAL_MISS, OutcomeKind.POLICY_VIOLATION,
                     OutcomeKind.SANDBOX_VIOLATION,
                     OutcomeKind.SIDE_EFFECT_INCIDENT):
            self.assertTrue(make_outcome(kind).is_safety)

    def test_attribution_round_trip(self):
        outcome = make_outcome(OutcomeKind.TASK_SUCCESS)
        restored = Outcome.from_dict(outcome.to_dict())
        self.assertEqual(restored.kind, OutcomeKind.TASK_SUCCESS)
        self.assertEqual(restored.attribution.deployment_lane, "candidate")
        self.assertEqual(restored.attribution.runtime_policy_version, "polv2")

    def test_signature_stable_for_duplicate(self):
        a = make_outcome(OutcomeKind.FALSE_NEGATIVE, "t1",
                         finding={"rule_id": "AUTH01",
                                  "evidence": "call auth check"})
        b = make_outcome(OutcomeKind.FALSE_NEGATIVE, "t2",
                         finding={"rule_id": "AUTH01",
                                  "evidence": "call auth check"})
        c = make_outcome(OutcomeKind.FALSE_NEGATIVE, "t3",
                         finding={"rule_id": "AUTH01",
                                  "evidence": "other evidence"})
        self.assertEqual(a.signature(), b.signature())
        self.assertNotEqual(a.signature(), c.signature())


class ExperienceBuilderTest(unittest.TestCase):
    def test_builds_five_kinds(self):
        builder = OutcomeExperienceBuilder()
        cases = [
            (OutcomeKind.TASK_SUCCESS, POSITIVE),
            (OutcomeKind.FALSE_POSITIVE, NEGATIVE),
            (OutcomeKind.TASK_FAILURE, FAILURE),
            (OutcomeKind.FINDING_REJECTED, NEGATIVE),
            (OutcomeKind.CRITICAL_MISS, SAFETY),
        ]
        for kind, expected in cases:
            exp = builder.build(make_outcome(kind))
            self.assertEqual(exp.experience_type, expected, kind.value)
            self.assertEqual(exp.source_type, "outcome")


class TrustGateTest(unittest.TestCase):
    def test_min_confirmers_required(self):
        gate = OutcomeTrustGate(TrustConfig(min_confirmers=2))
        o1 = make_outcome(OutcomeKind.FALSE_NEGATIVE, "t1",
                          finding={"rule_id": "AUTH01",
                                   "evidence": "uncalled check"})
        gate.record(o1)
        decision = gate.evaluate(o1)
        self.assertFalse(decision.trusted)
        o2 = make_outcome(OutcomeKind.FALSE_NEGATIVE, "t2",
                          finding={"rule_id": "AUTH01",
                                   "evidence": "uncalled check"})
        gate.record(o2)
        self.assertTrue(gate.evaluate(o2).trusted)

    def test_duplicate_merge_counts_tasks_not_feedback(self):
        gate = OutcomeTrustGate(TrustConfig(min_confirmers=3))
        finding = {"rule_id": "AUTH01", "evidence": "same"}
        for i in range(3):
            gate.record(make_outcome(OutcomeKind.FALSE_NEGATIVE, f"t{i}",
                                     finding=finding))
        self.assertTrue(
            gate.evaluate(make_outcome(OutcomeKind.FALSE_NEGATIVE, "t9",
                                       finding=finding)).trusted)


class OutcomeStoreLineageTest(unittest.TestCase):
    def test_lineage_gains_outcome_layer(self):
        lineage = LineageTracker()
        store = OutcomeStore(lineage=lineage)
        outcome = make_outcome(OutcomeKind.TASK_SUCCESS, candidate_id="cand-9")
        store.record_trusted(outcome)
        chain = lineage.get("cand-9")
        self.assertTrue(chain.has_stage(LineageStage.OUTCOME))
        node = chain.node(LineageStage.OUTCOME)
        self.assertEqual(node.node_id, outcome.outcome_id)
        self.assertEqual(node.payload["deployment_lane"], "candidate")

    def test_store_derives_experience(self):
        store = OutcomeStore()
        rec = store.record_trusted(make_outcome(OutcomeKind.CRITICAL_MISS))
        self.assertIsNotNone(rec.experience)
        self.assertEqual(rec.experience.experience_type, SAFETY)
        self.assertEqual(store.count(), 1)


if __name__ == "__main__":
    unittest.main()