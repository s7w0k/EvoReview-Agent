"""Tests for evolution lineage tracking (plan section 10.1-10.3)."""
import unittest

from evoagent.evolution_gov.lineage import (
    EvolutionLineage,
    LineageNode,
    LineageStage,
    LineageTracker,
)


class EvolutionLineageTest(unittest.TestCase):

    def test_full_chain_is_recorded_in_order(self):
        lineage = EvolutionLineage(
            "procedure-auth-v4",
            source_ids=["feedback-128", "failure-case-43", "trace-902"])
        lineage.add_stage(LineageStage.REFLECTION, "R-018")
        lineage.add_stage(LineageStage.HYPOTHESIS, "H-033",
                          failure_cases=17, human_feedback=8)
        lineage.add_stage(LineageStage.CANDIDATE, "procedure-auth-v4")
        lineage.add_stage(LineageStage.EVALUATION, "EV-443")
        lineage.add_stage(LineageStage.DEPLOYMENT, "DEP-91")
        lineage.add_stage(LineageStage.OUTCOME, "OUT-91")

        chain = lineage.chain()
        self.assertEqual(chain, [
            LineageStage.EXPERIENCE,
            LineageStage.REFLECTION,
            LineageStage.HYPOTHESIS,
            LineageStage.CANDIDATE,
            LineageStage.EVALUATION,
            LineageStage.DEPLOYMENT,
            LineageStage.OUTCOME,
        ])
        self.assertTrue(lineage.has_stage(LineageStage.DEPLOYMENT))

    def test_reported_sources(self):
        lineage = EvolutionLineage("c", source_ids=["a", "b"])
        self.assertEqual(lineage.reported_sources(), ["a", "b"])

    def test_tracker_assembles_and_retrieves(self):
        tracker = LineageTracker()
        tracker.begin("cand-1", source_ids=["feedback-1"])
        tracker.add("cand-1", LineageStage.REFLECTION, "R-1")
        tracker.add("cand-1", LineageStage.HYPOTHESIS, "H-1")
        lineage = tracker.get("cand-1")
        self.assertIsNotNone(lineage)
        self.assertIn(LineageStage.HYPOTHESIS, lineage.chain())
        self.assertEqual(len(tracker), 1)

    def test_chain_preserves_order_not_input_order_for_unsorted(self):
        lineage = EvolutionLineage("c")
        lineage.add_stage(LineageStage.OUTCOME, "o")
        lineage.add_stage(LineageStage.CANDIDATE, "k")
        # Sorted by canonical stage order regardless of insertion order.
        self.assertEqual(lineage.chain(),
                         [LineageStage.CANDIDATE, LineageStage.OUTCOME])

    def test_missing_stage_node_returns_none(self):
        lineage = EvolutionLineage("c")
        self.assertIsNone(lineage.node(LineageStage.HYPOTHESIS))


if __name__ == "__main__":
    unittest.main()