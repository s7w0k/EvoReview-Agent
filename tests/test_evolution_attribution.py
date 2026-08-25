"""Tests for evolution attribution reports (plan section 10.4)."""
import unittest

from evoagent.evolution_gov.attribution import (
    AttributionReport,
    AttributionReportBuilder,
    EvidenceCounts,
    ProductionOutcome,
    ReplayOutcome,
    render_attribution,
)
from evoagent.evolution_gov.lineage import (
    EvolutionLineage,
    LineageStage,
)


class EvolutionAttributionTest(unittest.TestCase):

    def test_builder_merges_evidence_from_lineage(self):
        lineage = EvolutionLineage("c", source_ids=["failure-case-43"])
        lineage.add_stage(LineageStage.REFLECTION, "R-1", failure_cases=17)
        lineage.add_stage(LineageStage.HYPOTHESIS, "H-1", human_feedback=8,
                          critic_objections=11)
        report = AttributionReportBuilder(lineage).build(
            evolution="procedure-auth-v3 -> v4",
            reason="Repeated false negatives on missing authorization checks.",
            change="Added caller inspection before verification.",
            replay=ReplayOutcome(tp_delta=9, fp_delta=1,
                                 high_risk_recall_delta=0.052,
                                 latency_delta=0.041),
        )
        self.assertEqual(report.evidence.failure_cases, 17)
        self.assertEqual(report.evidence.human_feedback, 8)
        self.assertEqual(report.evidence.critic_objections, 11)

    def test_render_sections(self):
        report = AttributionReportBuilder().build(
            evolution="a -> b", reason="because",
            change="added check",
            replay=ReplayOutcome(tp_delta=2, latency_delta=0.01),
            production=ProductionOutcome(accepted_findings_delta=0.038,
                                         user_rejection_delta=-0.024))
        text = render_attribution(report)
        self.assertIn("Evolution: a -> b", text)
        self.assertIn("Reason", text)
        self.assertIn("Replay Outcome", text)
        self.assertIn("Production Outcome", text)
        self.assertIn("KEEP", text)

    def test_report_to_dict(self):
        report = AttributionReport(
            evolution="x", reason="r", evidence=EvidenceCounts(failure_cases=3))
        data = report.to_dict()
        self.assertEqual(data["evolution"], "x")
        self.assertEqual(data["evidence"]["failure_cases"], 3)


if __name__ == "__main__":
    unittest.main()