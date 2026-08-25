"""Outcome log + lineage OUTCOME wiring (plan section 13.5).

``OutcomeStore`` records every production outcome and, when given an
``EvolutionLineage``, appends an ``OUTCOME`` layer carrying the attribution so
the full chain ``Experience -> ... -> Deployment -> Outcome`` closes.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..evolution_gov.lineage import LineageNode, LineageStage, LineageTracker
from .builder import OutcomeExperience, OutcomeExperienceBuilder
from .outcome import Outcome
from .trust import OutcomeTrustGate


@dataclass
class OutcomeRecord:
    """An outcome plus the (optional) experience derived from it."""

    outcome: Outcome
    experience: Optional[OutcomeExperience] = None

    def to_dict(self) -> Dict[str, Any]:
        value = self.outcome.to_dict()
        value["experience"] = (
            self.experience.to_dict() if self.experience else None)
        return value


class OutcomeStore:
    """Persists outcomes, derives experiences, and closes the lineage chain."""

    def __init__(
        self,
        *,
        builder: Optional[OutcomeExperienceBuilder] = None,
        trust_gate: Optional[OutcomeTrustGate] = None,
        lineage: Optional[LineageTracker] = None,
        build_experiences: bool = True,
    ):
        self._builder = builder or OutcomeExperienceBuilder()
        self._trust = trust_gate or OutcomeTrustGate()
        self._lineage = lineage
        self._build_experiences = build_experiences
        self._records: Dict[str, OutcomeRecord] = {}

    def record(self, outcome: Outcome) -> OutcomeRecord:
        record = OutcomeRecord(outcome=outcome)
        if self._build_experiences:
            record.experience = self._builder.build(outcome)
        self._records[outcome.outcome_id] = record
        self._append_to_lineage(outcome)
        return record

    def record_trusted(self, outcome: Outcome) -> OutcomeRecord:
        """Record an outcome and action its confirmation when trusted."""
        self._trust.record(outcome)
        return self.record(outcome)

    def trust_decision(self, outcome: Outcome):
        return self._trust.evaluate(outcome)

    def trust_actioned(self, outcome: Outcome) -> None:
        self._trust.actioned(outcome)

    def all(self) -> List[OutcomeRecord]:
        return list(self._records.values())

    def count(self) -> int:
        return len(self._records)

    # -- internals ----------------------------------------------------------

    def _append_to_lineage(self, outcome: Outcome) -> None:
        if self._lineage is None:
            return
        candidate_id = outcome.attribution.candidate_id or "unattributed"
        lineage = self._lineage.begin(candidate_id)
        lineage.add(LineageNode(
            stage=LineageStage.OUTCOME,
            node_id=outcome.outcome_id,
            source_refs=[outcome.attribution.candidate_id]
                        if outcome.attribution.candidate_id else [],
            payload={
                "task_id": outcome.task_id,
                "kind": outcome.kind.value,
                "deployment_lane": outcome.attribution.deployment_lane,
                "runtime_policy_version":
                    outcome.attribution.runtime_policy_version,
            },
        ))