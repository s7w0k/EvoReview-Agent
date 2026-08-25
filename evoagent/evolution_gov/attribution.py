"""Human-readable attribution report for an evolution change.

The report explains *why* an evolution happened and *with what outcome*
(section 10.4 of the plan), grounded in the recorded lineage, evidence counts,
replay changes and production outcome.
"""
from dataclasses import dataclass, field
from typing import Optional

from .lineage import EvolutionLineage, LineageStage


@dataclass
class EvidenceCounts:
    failure_cases: int = 0
    human_feedback: int = 0
    critic_objections: int = 0


@dataclass
class ReplayOutcome:
    tp_delta: int = 0
    fp_delta: int = 0
    high_risk_recall_delta: float = 0.0
    latency_delta: float = 0.0


@dataclass
class ProductionOutcome:
    accepted_findings_delta: float = 0.0
    user_rejection_delta: float = 0.0


@dataclass
class AttributionReport:
    """A complete attribution record for one evolution decision."""

    evolution: str                  # e.g. "procedure-auth-v3 -> v4"
    reason: str = ""
    evidence: EvidenceCounts = field(default_factory=EvidenceCounts)
    change: str = ""
    replay: Optional[ReplayOutcome] = None
    production: Optional[ProductionOutcome] = None
    decision: str = "KEEP"          # KEEP | ROLLBACK

    def to_dict(self) -> dict:
        return {
            "evolution": self.evolution,
            "reason": self.reason,
            "evidence": {
                "failure_cases": self.evidence.failure_cases,
                "human_feedback": self.evidence.human_feedback,
                "critic_objections": self.evidence.critic_objections,
            },
            "change": self.change,
            "replay": None if self.replay is None else self.replay.__dict__,
            "production": None if self.production is None else self.production.__dict__,
            "decision": self.decision,
        }


class AttributionReportBuilder:
    """Builds an ``AttributionReport`` from a lineage and supporting data."""

    def __init__(self, lineage: Optional[EvolutionLineage] = None):
        self._lineage = lineage

    def build(
        self,
        evolution: str,
        reason: str,
        evidence: Optional[EvidenceCounts] = None,
        change: str = "",
        replay: Optional[ReplayOutcome] = None,
        production: Optional[ProductionOutcome] = None,
        decision: str = "KEEP",
    ) -> AttributionReport:
        evidence = evidence or EvidenceCounts()
        # Merge evidence counts from reflection + hypothesis nodes if not given.
        if not any((evidence.failure_cases, evidence.human_feedback,
                    evidence.critic_objections)):
            payload = self._collect_payload()
            merged = EvidenceCounts(
                failure_cases=payload.get("failure_cases", 0),
                human_feedback=payload.get("human_feedback", 0),
                critic_objections=payload.get("critic_objections", 0),
            )
            evidence = merged

        return AttributionReport(
            evolution=evolution,
            reason=reason,
            evidence=evidence,
            change=change,
            replay=replay,
            production=production,
            decision=decision,
        )

    def _collect_payload(self) -> dict:
        merged: dict = {}
        if self._lineage is None:
            return merged
        for node in self._lineage.nodes:
            if node.stage in (LineageStage.REFLECTION, LineageStage.HYPOTHESIS,
                              LineageStage.EXPERIENCE):
                for key, value in node.payload.items():
                    if value is None:
                        continue
                    if isinstance(value, (int, float)) and key in merged:
                        merged[key] = merged[key] + value
                    else:
                        merged[key] = value
        return merged


def render_attribution(report: AttributionReport) -> str:
    """Render the attribution report as readable text (see plan section 10.4)."""
    lines = [f"Evolution: {report.evolution}", ""]
    lines += ["Reason", "------", report.reason, ""]
    lines += ["Evidence", "--------",
              f"Failure cases: {report.evidence.failure_cases}",
              f"Human feedback: {report.evidence.human_feedback}",
              f"Critic objections: {report.evidence.critic_objections}", ""]
    lines += ["Change", "------", report.change, ""]
    if report.replay is not None:
        lines += ["Replay Outcome", "--------------",
                  f"TP {report.replay.tp_delta:+d}",
                  f"FP {report.replay.fp_delta:+d}",
                  f"High-risk recall {report.replay.high_risk_recall_delta:+.1%}",
                  f"Latency {report.replay.latency_delta:+.1%}", ""]
    if report.production is not None:
        lines += ["Production Outcome", "------------------",
                  f"Accepted findings {report.production.accepted_findings_delta:+.1%}",
                  f"User rejection {report.production.user_rejection_delta:+.1%}", ""]
    lines += ["Decision", "--------", report.decision]
    return "\n".join(lines)