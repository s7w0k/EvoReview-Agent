"""Automatic rollback when a canary candidate regresses.

A candidate that passed replay and entered canary is still only provisionally
adopted.  If its production behaviour drifts into regression on any hard metric
-- reliability, failure rate, or a safety-relevant degradation -- it is rolled
back automatically instead of being left to degrade reviews.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from .objective import EvolutionMetrics


@dataclass
class RollbackDecision:
    """A decision to keep / roll back a live candidate policy."""

    should_rollback: bool
    reasons: List[str] = field(default_factory=list)

    @property
    def keep(self) -> bool:
        return not self.should_rollback


@dataclass
class RollbackThresholds:
    failure_rate_ceiling: float = 0.01       # >= this failure rate -> roll back
    reliability_floor: float = 0.90          # reliability below this -> roll back
    quality_regression: float = 0.02         # allowed quality loss vs baseline
    recall_regression: float = 0.01          # allowed high-risk recall loss


class AutoRollback:
    """Compares live candidate metrics against baseline to decide a rollback."""

    def __init__(self, thresholds: Optional[RollbackThresholds] = None):
        self._thresholds = thresholds or RollbackThresholds()

    def evaluate(
        self,
        baseline: EvolutionMetrics,
        candidate: EvolutionMetrics,
    ) -> RollbackDecision:
        """Return whether the candidate should be rolled back."""
        reasons: List[str] = []
        t = self._thresholds

        if candidate.failure_rate >= t.failure_rate_ceiling:
            reasons.append(
                f"failure_rate {candidate.failure_rate:.3f} >= "
                f"ceiling {t.failure_rate_ceiling}")
        if candidate.reliability_score < t.reliability_floor:
            reasons.append(
                f"reliability {candidate.reliability_score:.3f} < "
                f"floor {t.reliability_floor}")
        if baseline.quality_score - candidate.quality_score > t.quality_regression:
            reasons.append(
                "quality fell more than "
                f"{t.quality_regression} vs baseline")
        if baseline.high_risk_recall - candidate.high_risk_recall > t.recall_regression:
            reasons.append(
                "high-risk recall fell more than "
                f"{t.recall_regression} vs baseline")
        if candidate.critical_misses > 0:
            reasons.append(
                f"candidate introduced {candidate.critical_misses} critical miss(es)")

        return RollbackDecision(
            should_rollback=bool(reasons), reasons=reasons)