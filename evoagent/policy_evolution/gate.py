"""Hard safety gate for policy evolution.

Per the plan (section 9.4) *Safety Constraints take precedence over
Optimization Score*.  A candidate is rejected outright if it drops high-risk
recall beyond a tolerance or introduces any critical miss, regardless of how
much cost or latency it saves.
"""
from dataclasses import dataclass
from typing import List, Optional

from .objective import EvolutionMetrics


@dataclass
class GateDecision:
    """Outcome of evaluating a candidate against the hard safety gate."""

    approved: bool
    reasons: List[str] = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []

    @property
    def rejected(self) -> bool:
        return not self.approved


class EvolutionGate:
    """Deterministic hard-gate against regressions in safety-critical metrics."""

    def __init__(
        self,
        high_risk_recall_tolerance: float = 0.01,   # 1 percentage point
        require_zero_critical_misses: bool = True,
        reliability_tolerance: float = 0.0,
        require_zero_policy_violations: bool = True,
        require_zero_side_effect_incidents: bool = True,
    ):
        if high_risk_recall_tolerance < 0:
            raise ValueError("high_risk_recall_tolerance cannot be negative")
        self._recall_tolerance = high_risk_recall_tolerance
        self._require_zero_critical = require_zero_critical_misses
        self._reliability_tolerance = reliability_tolerance
        self._require_zero_policy_violations = require_zero_policy_violations
        self._require_zero_side_effect = require_zero_side_effect_incidents

    def evaluate(
        self,
        baseline: EvolutionMetrics,
        candidate: EvolutionMetrics,
    ) -> GateDecision:
        """Compare candidate vs baseline and return a pass / reject decision."""
        reasons: List[str] = []

        if self._require_zero_critical and candidate.critical_misses > 0:
            reasons.append(
                f"critical miss present: {candidate.critical_misses} (must be 0)")

        recall_loss = baseline.high_risk_recall - candidate.high_risk_recall
        if recall_loss > self._recall_tolerance:
            reasons.append(
                "high-risk recall dropped by "
                f"{recall_loss:.4f} > tolerance {self._recall_tolerance}")

        reliability_loss = baseline.reliability_score - candidate.reliability_score
        if reliability_loss > self._reliability_tolerance:
            reasons.append(
                "reliability dropped by "
                f"{reliability_loss:.4f} > tolerance {self._reliability_tolerance}")

        if self._require_zero_policy_violations and candidate.policy_violations > 0:
            reasons.append(
                "policy violation present: "
                f"{candidate.policy_violations} (must be 0)")

        if self._require_zero_side_effect and candidate.side_effect_safety_incidents > 0:
            reasons.append(
                "side-effect safety incident present: "
                f"{candidate.side_effect_safety_incidents} (must be 0)")

        return GateDecision(approved=not reasons, reasons=reasons)